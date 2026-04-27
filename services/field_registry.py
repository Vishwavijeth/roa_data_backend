from .comparison import (
    compare_values,
    compare_names,
    compare_buying_agent,
    compare_transaction_specialist
)

FIELD_MAP = {
    # "sale_price": {
    #     "be": "sale_price",
    #     "ss": "saleprice",
    #     "compare": compare_values
    # },
    # "close_date": {
    #     "be": "closed_date",
    #     "ss": "escrowclosingdate",
    #     "compare": compare_values
    # },
    # "contract_date": {
    #     "be": "contract_date",
    #     "ss": "contractacceptancedate",
    #     "compare": compare_values
    # },
    # "listing_price": {
    #     "be": "listing_price",
    #     "ss": "listingprice",
    #     "compare": compare_values
    # },
    "buyer_name": {
        "be": "buyer_name",
        "ss": "buyer_full_name",
        "compare": compare_names
    },
    "seller_name": {
        "be": "seller_name",
        "ss": "seller_full_name",
        "compare": compare_names
    },
    # "gross_commission": {
    #     "be": "total_gross_commission",
    #     "ss": "officegrosscommissiononsale",
    #     "compare": compare_names
    # },
    "buying_agent_name": {
        "be": "buying_agent_name",
        "ss": "skyslope_buying_agent_name",
        "compare": compare_buying_agent
    },
    # "transaction_specialist": {
    #     "be": "transaction_specialist",
    #     "ss": "reviewer_full_name",
    #     "compare": compare_transaction_specialist
    # }
    "title_company": {
    "be": "da_title_company",
    "ss": "title_company",
    "compare": compare_names
    },
}