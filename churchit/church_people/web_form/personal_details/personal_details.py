from churchit.church_people.member_access import limit_to_personal_details


def get_context(context):
	limit_to_personal_details(context)
