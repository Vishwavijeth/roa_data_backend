from sqlalchemy import and_, func, literal, or_, select, true, false

from models.brokerage_engine.sale_transactions import BESaleTransactions
from models.brokerage_engine.other_income_transactions import BEOtherIncomeTransaction
from models.reconciliation_data import ReconciliationData


def build_matched_transactions_subquery(target_emails: list[str], target_names: list[str]):
    """
    Returns one row per (agent_key, transaction_id, property_address, source_status, source_name).

    agent_key is either a matched buying_agent_email (brokerage_engine)
    or a matched agents name (otherincome_transactions).

    Works for one agent (detail page, single-item lists) or many agents
    (listing page, page-sized lists) since target_emails / target_names
    are just filtered with IN.
    """

    brokerage_matches = None
    other_income_matches = None

    if target_emails:
        split_emails = (
            func.unnest(func.string_to_array(BESaleTransactions.buying_agent_email, ","))
            .table_valued("email")
            .render_derived()
            .lateral()
        )

        brokerage_matches = (
            select(
                func.trim(split_emails.c.email).label("agent_key"),
                BESaleTransactions.transaction_identifier_transactionid.label("transaction_id"),
                BESaleTransactions.property_address.label("property_address"),
                BESaleTransactions.transaction_status.label("source_status"),
                literal("brokerage_engine").label("source_name"),
            )
            .select_from(BESaleTransactions)
            .join(split_emails, true())
            .where(func.trim(split_emails.c.email).in_(target_emails))
        )

    if target_names:
        split_names = (
            func.unnest(func.string_to_array(BEOtherIncomeTransaction.agents, ","))
            .table_valued("agent_name")
            .render_derived()
            .lateral()
        )

        other_income_matches = (
            select(
                func.trim(split_names.c.agent_name).label("agent_key"),
                BEOtherIncomeTransaction.transaction_identifier_transactionid.label("transaction_id"),
                BEOtherIncomeTransaction.property_address.label("property_address"),
                BEOtherIncomeTransaction.transaction_status.label("source_status"),
                literal("otherincome_transactions").label("source_name"),
            )
            .select_from(BEOtherIncomeTransaction)
            .join(split_names, true())
            .where(func.trim(split_names.c.agent_name).in_(target_names))
        )

    if brokerage_matches is not None and other_income_matches is not None:
        return brokerage_matches.union(other_income_matches).subquery("matched_transactions")
    if brokerage_matches is not None:
        return brokerage_matches.subquery("matched_transactions")
    if other_income_matches is not None:
        return other_income_matches.subquery("matched_transactions")

    return select(
        literal(None).label("agent_key"),
        literal(None).label("transaction_id"),
        literal(None).label("property_address"),
        literal(None).label("source_status"),
        literal(None).label("source_name"),
    ).where(false()).subquery("matched_transactions")


def build_latest_reconciliation_subquery():
    """
    One row per transactionid — the most recently evaluated reconciliation record.
    """

    row_number_col = (
        func.row_number()
        .over(
            partition_by=ReconciliationData.transactionid,
            order_by=ReconciliationData.evaluated_at.desc().nullslast(),
        )
        .label("row_number")
    )

    ranked = select(
        ReconciliationData.transactionid.label("transactionid"),
        ReconciliationData.be_source_table.label("be_source_table"),
        ReconciliationData.saleguid.label("saleguid"),
        ReconciliationData.be_transaction_specialist.label("be_transaction_specialist"),
        ReconciliationData.skyslope_reviewer.label("skyslope_reviewer"),
        ReconciliationData.be_gross_commission.label("be_gross_commission"),
        ReconciliationData.skyslope_gross_commission.label("skyslope_gross_commission"),
        ReconciliationData.gross_commission_match.label("gross_commission_match"),
        ReconciliationData.be_close_date_value.label("be_close_date_value"),
        ReconciliationData.skyslope_close_date_value.label("skyslope_close_date_value"),
        ReconciliationData.close_date_match.label("close_date_match"),
        ReconciliationData.be_status_value.label("be_status_value"),
        ReconciliationData.skyslope_status_value.label("skyslope_status_value"),
        ReconciliationData.status_match.label("status_match"),
        ReconciliationData.be_sale_price.label("be_sale_price"),
        ReconciliationData.skyslope_sale_price.label("skyslope_sale_price"),
        ReconciliationData.sale_price_match.label("sale_price_match"),
        row_number_col,
    ).subquery("reconciliation_ranked")

    return select(ranked).where(ranked.c.row_number == 1).subquery("latest_reconciliation")


def build_mismatch_expression(latest_reconciliation):
    def not_match(column):
        return and_(column.isnot(None), func.lower(func.trim(column)) != "match")

    return or_(
        latest_reconciliation.c.transactionid.is_(None),
        not_match(latest_reconciliation.c.gross_commission_match),
        not_match(latest_reconciliation.c.close_date_match),
        not_match(latest_reconciliation.c.status_match),
        not_match(latest_reconciliation.c.sale_price_match),
    )