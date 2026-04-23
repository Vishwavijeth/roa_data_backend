from services.comparison import compare_values, compare_names, compare_contains

FIELD_MAP = {
    "sale_price": {
        "be": "sale_price",
        "ss": "saleprice",
        "comparator": compare_values
    },
    "close_date": {
        "be": "closed_date",
        "ss": "escrowclosingdate",
        "comparator": compare_values
    },
    "listing_price": {
        "be": "listing_price",
        "ss": "listingprice",
        "comparator": compare_values
    },
    "buyer_name": {
        "be": "buyer_name",
        "ss": "buyer_full_name",
        "comparator": compare_names
    },
    "seller_name": {
        "be": "seller_name",
        "ss": "seller_full_name",
        "comparator": compare_names
    },
    "buying_agent": {
        "be": "buying_agent_name",
        "ss": "agent_full_name",
        "comparator": compare_contains
    },
    "gross_commission": {
        "be": "total_gross_commission",
        "ss": "officeGrossCommissionOnSale",
        "comparator": compare_values
    },
    "reviewer": {
        "be": "reviewer_full_name",
        "ss": "reviewer_full_name",
        "comparator": compare_names
    }
}