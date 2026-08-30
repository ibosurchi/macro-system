from apex.bootstrap import prepare_page
from apex.auth import require_auth
from apex.views import render_nasdaq

prepare_page()
auth_user = require_auth()
render_nasdaq(auth_user)
