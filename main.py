"""BITStore App Main module."""

import jinja2
import json
import os
import webapp2

from google.appengine.api import users

from bitstoreapiclient import BITStore

jinja = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

PARAMS = {
    'api_key': 'AIzaSyDzRhwd2xw77iyyp0acSEB3yNgNhdntAV0',
    # 'base_url': 'http://karlsson.c.broad-karlsson.internal:8081/_ah/api',
    # 'debug': True,
}


def is_dev():
    """Return true if this is the development environment."""
    dev = False
    if os.environ['SERVER_SOFTWARE'].startswith('Development'):
        dev = True
    return dev


def render_theme(body, request):
    """Render the main template header and footer."""
    template = jinja.get_template('theme.html')
    return template.render(
        body=body,
        is_admin=users.is_current_user_admin(),
        is_dev=is_dev(),
        request=request,
    )


class AdminPage(webapp2.RequestHandler):
    """Class for AdminPage."""

    def get(self):
        """Return the admin page."""
        template_values = {

        }
        template = jinja.get_template('admin.html')
        body = template.render(template_values)
        output = render_theme(body, self.request)
        self.response.write(output)


class MainPage(webapp2.RequestHandler):
    """Class for MainPage."""

    def get(self):
        """Return the main page."""
        b = BITStore(**PARAMS)
        filesystems = b.get_filesystems()
        print(json.dumps(filesystems, indent=2, sort_keys=True))
        template_values = {
            'filesystems': filesystems,
        }
        template = jinja.get_template('index.html')
        body = template.render(template_values)
        output = render_theme(body, self.request)
        self.response.write(output)


app = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/admin', AdminPage),
], debug=True)
