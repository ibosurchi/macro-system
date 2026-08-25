from apex.bootstrap import prepare_page
from apex.auth import require_admin
from apex.views import render_admin

prepare_page()
auth_user = require_admin()
render_admin(auth_user)
