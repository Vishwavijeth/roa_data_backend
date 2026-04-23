from decimal import Decimal
import re

def normalize_value(val):
    if val is None or val == '' or val == 0:
        return None
    return val

def compare_values(sale_val, be_val):
    sale_val = normalize_value(sale_val)
    be_val = normalize_value(be_val)

    if sale_val is None or be_val is None:
        return 'null'

    if isinstance(sale_val, (int, float, Decimal)) and isinstance(be_val, (int, float, Decimal)):
        return 'match' if abs(float(sale_val) - float(be_val)) < 0.0001 else 'mismatch'

    return 'match' if str(sale_val) == str(be_val) else 'mismatch'

NULL_NAME_STRINGS = {'na', 'n/a', 'na na', '-', '--', '---'}

def compare_buying_agent(be_value, skyslope_value):
    be_value = normalize_value(be_value)
    skyslope_value = normalize_value(skyslope_value)

    if be_value is None or skyslope_value is None:
        return 'null'

    be_clean = canonical_name(be_value)
    skyslope_clean = canonical_name(skyslope_value)

    # extract full name patterns
    name_pattern = r'[a-z]+(?:\s+[a-z]+)+'

    be_names = re.findall(name_pattern, be_clean)
    skyslope_names = re.findall(name_pattern, skyslope_clean)

    if not be_names or not skyslope_names:
        return 'null'

    # SIMPLE RULE:
    # if ANY skyslope full name exists inside BE string → match
    for sk_name in skyslope_names:
        if sk_name in be_clean:
            return 'match'

    return 'mismatch'

def is_null_name(val):
    if val is None:
        return True

    val = str(val).strip().lower()

    if val in NULL_NAME_STRINGS:
        return True

    tokens = re.sub(r'\s+', ' ', val).split()
    if tokens and all(t in NULL_NAME_STRINGS for t in tokens):
        return True

    return False

def canonical_name(val):
    if val is None:
        return None

    val = str(val)

    # remove invisible chars
    val = re.sub(r'[\u200B-\u200D\uFEFF]', '', val)

    # normalize whitespace
    val = re.sub(r'\s+', ' ', val)

    val = val.replace('\u00A0', ' ')

    return val.strip().lower()

def split_names(val):
    """
    Works for BOTH sale and BE:
    - comma separated full names
    - ANY NA invalidates entire field
    """

    if val is None or str(val).strip() == '':
        return None

    parts = [p.strip() for p in str(val).split(',')]

    cleaned = []

    for p in parts:
        if is_null_name(p):
            return None  # strict rule

        cleaned.append(canonical_name(p))

    return cleaned if cleaned else None


def compare_names(sale_name, be_name):
    """
    FINAL RULE:
    - BOTH sides become lists of full names
    - match if ANY overlap exists
    - otherwise mismatch
    - if invalid/NA → null
    """

    sale_list = split_names(sale_name)
    be_list = split_names(be_name)

    if sale_list is None or be_list is None:
        return 'null'

    sale_set = set(sale_list)
    be_set = set(be_list)

    if sale_set.intersection(be_set):
        return 'match'

    return 'mismatch'


def compare_transaction_specialist(be, ss):
    if be is None or ss is None:
        return "null"
    return "match" if ss.lower() in be.lower() else "mismatch"