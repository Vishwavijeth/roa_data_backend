from .loaders import load_data


def run_brokerage_engine(brokerhold: bool = False):

    _, be_data = load_data(brokerhold)

    results = []

    for b in be_data:

        results.append({
            "transactionid": b.get("transaction_identifier_transactionid"),
            "property_address": b.get("property_address"),
            "buying_agent_name": b.get("buying_agent_name"),
            "sale_price": b.get("sale_price"),
            "contract_date": b.get("contract_date"),
            "close_date": b.get("closed_date"),
            "transaction_specialist": b.get("transaction_specialist"),
            "status": b.get("transaction_status"),
            "skyslopefileid": b.get("skyslopefileid")
        })

    return results

#skyslope api
def get_skyslope_data():
    sales, be_data = load_data()

    # skyslopefileid -> transaction_identifier_transactionid
    be_map = {
        b.get("skyslopefileid"): b.get("transaction_identifier_transactionid")
        for b in be_data
        if b.get("skyslopefileid") is not None
    }

    results = []

    for s in sales:
        sale_guid = s.get("saleguid")

        results.append({
            "saleguid": sale_guid,
            "transaction_id": be_map.get(sale_guid, ""),  # blank if no match
            "contract_date": s.get("contractacceptancedate"),
            "propertyaddress": s.get("propertyaddress"),
            "close_date": s.get("escrowclosingdate"),
            "buyer_name": s.get("buyer_full_name"),
            "buyer_agent_name": s.get("agent_full_name"),
            "status": s.get("status"),
            "reviewer": s.get("reviewer_full_name")
        })

    return results