from apex.bootstrap import prepare_page
from apex.auth import require_auth
from apex.views import render_dashboard

prepare_page()
auth_user = require_auth()
render_dashboard(auth_user)
