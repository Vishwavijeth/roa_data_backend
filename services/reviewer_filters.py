from services.state_office_mapping import STATE_OFFICES_MAP

def apply_common_filters(
    query,
    params,
    from_date=None,
    to_date=None,
    state=None,
    stage_name=None,
    status=None,
    reviewer=None,
    type_of_sale=None,
    date_field="s.escrowclosingdate",
    reviewer_expr="COALESCE(NULLIF(TRIM(CONCAT_WS(' ', r.firstname, r.lastname)), ''), 'Unassigned')",
):
    if from_date:
        query += f" AND {date_field} >= %s"
        params.append(from_date)

    if to_date:
        query += f" AND {date_field} <= %s"
        params.append(to_date)

    if state:
        cleaned_states = sorted({
            x.strip().upper()
            for x in state
            if x and x.strip()
        })

        if cleaned_states:
            mapped_offices = []
            for state_code in cleaned_states:
                mapped_offices.extend(STATE_OFFICES_MAP.get(state_code, []))

            mapped_offices = list({
                office_name.strip()
                for office_name in mapped_offices
                if office_name and office_name.strip()
            })

            if mapped_offices:
                query += " AND TRIM(COALESCE(o.officename, '')) = ANY(%s)"
                params.append(mapped_offices)
            else:
                query += " AND 1=0"

    if stage_name:
        cleaned_stage_names = [x.strip() for x in stage_name if x and x.strip()]
        if cleaned_stage_names:
            query += " AND st.name = ANY(%s)"
            params.append(cleaned_stage_names)

    if status:
        cleaned_status = [x.strip() for x in status if x and x.strip()]
        if cleaned_status:
            query += " AND s.status = ANY(%s)"
            params.append(cleaned_status)

    if reviewer:
        cleaned_reviewers = [x.strip() for x in reviewer if x and x.strip()]
        if cleaned_reviewers:
            non_unassigned_reviewers = [x for x in cleaned_reviewers if x != "Unassigned"]
            has_unassigned = "Unassigned" in cleaned_reviewers

            reviewer_conditions = []

            if non_unassigned_reviewers:
                reviewer_conditions.append(f"{reviewer_expr} = ANY(%s)")
                params.append(non_unassigned_reviewers)

            if has_unassigned:
                reviewer_conditions.append("s.reviewerguid IS NULL")

            if reviewer_conditions:
                query += " AND (" + " OR ".join(reviewer_conditions) + ")"

    if type_of_sale:
        cleaned_type_of_sale = [x.strip() for x in type_of_sale if x and x.strip()]
        if cleaned_type_of_sale:
            query += " AND s.dealtype = ANY(%s)"
            params.append(cleaned_type_of_sale)

    return query, params