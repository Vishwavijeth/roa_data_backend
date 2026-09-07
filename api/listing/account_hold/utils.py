from sqlalchemy import String, and_, case, false, func, literal, or_, select, true, type_coerce, union

from models.brokerage_engine.sale_transactions import BESaleTransactions
from models.brokerage_engine.other_income_transactions import BEOtherIncomeTransaction
from models.reconciliation_data import ReconciliationData


def normalize_agent_identifiers(agent_identifiers: list) -> list[str]:
    return list({
        str(agent_identifier).strip().lower()
        for agent_identifier in agent_identifiers
        if agent_identifier is not None and str(agent_identifier).strip()
    })


def build_matched_transactions_subquery(target_agent_identifiers: list):
    target_identifiers = normalize_agent_identifiers(target_agent_identifiers)

    if not target_identifiers:
        return (
            select(
                literal(None).label("agent_key"),
                literal(None).label("transaction_id"),
                literal(None).label("property_address"),
                literal(None).label("source_status"),
                literal(None).label("source_name"),
                literal(None).label("agent_net"),
            )
            .where(false())
            .subquery("matched_transactions")
        )

    buying_identifier_text = type_coerce(
        BESaleTransactions.buying_agent_identifier,
        String,
    )

    listing_identifier_text = type_coerce(
        BESaleTransactions.listing_agent_identifier,
        String,
    )

    other_income_identifier_text = type_coerce(
        BEOtherIncomeTransaction.agents_identifier,
        String,
    )

    tags = func.lower(
        func.coalesce(
            BESaleTransactions.tags,
            "",
        )
    )

    has_selling_side = tags.like("%sellingside%")
    has_listing_side = tags.like("%listingside%")

    buying_identifier_array = func.string_to_array(
        func.lower(
            func.replace(
                func.coalesce(
                    buying_identifier_text,
                    "",
                ),
                " ",
                "",
            )
        ),
        ",",
    )

    listing_identifier_array = func.string_to_array(
        func.lower(
            func.replace(
                func.coalesce(
                    listing_identifier_text,
                    "",
                ),
                " ",
                "",
            )
        ),
        ",",
    )

    split_buying_identifiers = (
        func.unnest(
            buying_identifier_array
        )
        .table_valued(
            "agent_identifier"
        )
        .render_derived()
        .lateral()
    )

    buying_agent_identifier = func.trim(
        split_buying_identifiers.c.agent_identifier
    )

    buying_agent_is_listing_agent = and_(
        has_listing_side,
        func.array_position(
            listing_identifier_array,
            buying_agent_identifier,
        ).is_not(None),
    )

    buying_agent_net = case(
        (
            buying_agent_is_listing_agent,
            BESaleTransactions.total_agent_net,
        ),
        else_=BESaleTransactions.buying_side_agent_net,
    )

    selling_side_matches = (
        select(
            buying_agent_identifier.label("agent_key"),
            BESaleTransactions.transaction_identifier_transactionid.label("transaction_id"),
            BESaleTransactions.property_address.label("property_address"),
            BESaleTransactions.transaction_status.label("source_status"),
            literal("brokerage_engine").label("source_name"),
            buying_agent_net.label("agent_net"),
        )
        .select_from(BESaleTransactions)
        .join(
            split_buying_identifiers,
            true(),
        )
        .where(
            has_selling_side,
            buying_agent_identifier.in_(
                target_identifiers
            ),
        )
    )

    split_listing_identifiers = (
        func.unnest(
            listing_identifier_array
        )
        .table_valued(
            "agent_identifier"
        )
        .render_derived()
        .lateral()
    )

    listing_agent_identifier = func.trim(
        split_listing_identifiers.c.agent_identifier
    )

    listing_agent_is_buying_agent = and_(
        has_selling_side,
        func.array_position(
            buying_identifier_array,
            listing_agent_identifier,
        ).is_not(None),
    )

    listing_agent_net = case(
        (
            listing_agent_is_buying_agent,
            BESaleTransactions.total_agent_net,
        ),
        else_=BESaleTransactions.listing_side_agent_net,
    )

    listing_side_matches = (
        select(
            listing_agent_identifier.label("agent_key"),
            BESaleTransactions.transaction_identifier_transactionid.label("transaction_id"),
            BESaleTransactions.property_address.label("property_address"),
            BESaleTransactions.transaction_status.label("source_status"),
            literal("brokerage_engine").label("source_name"),
            listing_agent_net.label("agent_net"),
        )
        .select_from(BESaleTransactions)
        .join(
            split_listing_identifiers,
            true(),
        )
        .where(
            has_listing_side,
            listing_agent_identifier.in_(
                target_identifiers
            ),
        )
    )

    normalized_other_income_identifier = func.lower(
        func.trim(
            other_income_identifier_text
        )
    )

    other_income_matches = (
        select(
            normalized_other_income_identifier.label("agent_key"),
            BEOtherIncomeTransaction.transaction_identifier_transactionid.label("transaction_id"),
            BEOtherIncomeTransaction.property_address.label("property_address"),
            BEOtherIncomeTransaction.transaction_status.label("source_status"),
            literal("otherincome_transactions").label("source_name"),
            BEOtherIncomeTransaction.agent_net.label("agent_net"),
        )
        .where(
            other_income_identifier_text.is_not(None),
            other_income_identifier_text != "",
            normalized_other_income_identifier.in_(
                target_identifiers
            ),
        )
    )

    return union(
        selling_side_matches,
        listing_side_matches,
        other_income_matches,
    ).subquery("matched_transactions")


def build_latest_reconciliation_subquery(
    matched_transactions,
):
    return (
        select(
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
        )
        .where(
            ReconciliationData.transactionid
            == matched_transactions.c.transaction_id
        )
        .order_by(
            ReconciliationData.evaluated_at
            .desc()
            .nullslast()
        )
        .limit(1)
        .lateral(
            "latest_reconciliation"
        )
    )


def build_mismatch_expression(
    latest_reconciliation,
):
    def not_match(column):
        return and_(
            column.is_not(None),
            func.lower(
                func.trim(column)
            ) != "match",
        )

    return or_(
        latest_reconciliation.c.transactionid.is_(None),
        not_match(
            latest_reconciliation.c.gross_commission_match
        ),
        not_match(
            latest_reconciliation.c.close_date_match
        ),
        not_match(
            latest_reconciliation.c.status_match
        ),
        not_match(
            latest_reconciliation.c.sale_price_match
        ),
    )


def build_agent_transactions_subquery(
    target_agent_identifiers: list,
):
    matched_transactions = (
        build_matched_transactions_subquery(
            target_agent_identifiers
        )
    )

    latest_reconciliation = (
        build_latest_reconciliation_subquery(
            matched_transactions
        )
    )

    mismatch_expression = (
        build_mismatch_expression(
            latest_reconciliation
        )
    )

    is_closed = (
        func.lower(
            func.trim(
                func.coalesce(
                    matched_transactions.c.source_status,
                    "",
                )
            )
        )
        == "closed"
    )

    return (
        select(
            matched_transactions.c.agent_key,
            matched_transactions.c.transaction_id,
            matched_transactions.c.property_address,
            matched_transactions.c.source_status,
            matched_transactions.c.source_name,
            matched_transactions.c.agent_net,
            is_closed.label("is_closed"),
            latest_reconciliation.c.transactionid.label(
                "reconciliation_transactionid"
            ),
            latest_reconciliation.c.be_source_table,
            latest_reconciliation.c.saleguid,
            latest_reconciliation.c.be_transaction_specialist,
            latest_reconciliation.c.skyslope_reviewer,
            latest_reconciliation.c.be_gross_commission,
            latest_reconciliation.c.skyslope_gross_commission,
            latest_reconciliation.c.gross_commission_match,
            latest_reconciliation.c.be_close_date_value,
            latest_reconciliation.c.skyslope_close_date_value,
            latest_reconciliation.c.close_date_match,
            latest_reconciliation.c.be_status_value,
            latest_reconciliation.c.skyslope_status_value,
            latest_reconciliation.c.status_match,
            latest_reconciliation.c.be_sale_price,
            latest_reconciliation.c.skyslope_sale_price,
            latest_reconciliation.c.sale_price_match,
            mismatch_expression.label(
                "has_transaction_mismatch"
            ),
        )
        .select_from(
            matched_transactions
        )
        .outerjoin(
            latest_reconciliation,
            true(),
        )
        .subquery(
            "agent_transactions"
        )
    )