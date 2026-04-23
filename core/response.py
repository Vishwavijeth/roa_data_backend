def build_response(s, b, be_val, ss_val, result):
    return {
        "saleguid": s.get("saleguid"),
        "transactionid": b.get("transaction_identifier_transactionid"),
        "propertyaddress": b.get("property_address"),

        "be_sale_price": be_val,
        "skyslope_sale_price": ss_val,

        "match_result": result
    }