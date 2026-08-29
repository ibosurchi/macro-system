from apex.bootstrap import prepare_page
from apex.auth import require_auth
from apex.views import render_gold

prepare_page()
auth_user = require_auth()
render_gold(auth_user)
