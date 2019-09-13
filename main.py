"""BITStore App Main module."""

import jinja2
import json
import os
import webapp2
import datetime
import google.auth
from google.appengine.api import users

from bitstoreapiclient import BITStore
from config import api, api_key, base_url, debug

todays_date = datetime.datetime.today()

jinja = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

PARAMS = {
    'api': api,
    'api_key': api_key,
    'base_url': base_url,
    'debug': debug,
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
        request=request
    )

def fs_list_to_dict(filesystems):
    """
    Convert a list of filesystem objects to a dict
    with each key being the FS and the object being the object
    """
    fs_dict = {}
    for fs in filesystems:
        fs_dict[fs['fs']] = fs

    return fs_dict

def storage_class_list_to_dict(storage_classes):
    """
    Convert a list of storage class objects to a dict
    with each key being the id and the object being the object
    """
    sc_dict = {}
    for sc in storage_classes:
        sc_dict[sc['id']] = sc

    return sc_dict


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


class FilesystemEditPage(webapp2.RequestHandler):
    """Class for FilesystemEditPage."""

    def get(self, filesystem_id):
        """Return the filesystem edit page."""
        b = BITStore(**PARAMS)
        filesystem = b.get_filesystem(filesystem_id)
        storageclasses = b.get_storageclasses()
        template = jinja.get_template('admin-filesystem.html')
        body = template.render(
            edit=True,
            filesystem=filesystem,
            fs=filesystem['fs'],
            json=json.dumps(filesystem, indent=2, sort_keys=True),
            storageclasses=sorted(storageclasses, key=lambda x: x['name']),
        )
        output = render_theme(body, self.request)
        self.response.write(output)

    def post(self, filesystem_id):
        """Update a filesystem."""
        b = BITStore(**PARAMS)
        filesystem = b.get_filesystem(filesystem_id)
        post_data = dict(self.request.POST)

        print('Initial Post Data: {}'.format(post_data))

        # check active
        if 'active' in post_data:
            post_data['active'] = True
        else:
            post_data['active'] = False

        print('Active Post Data: {}'.format(post_data))

        # fields to potentially update
        fields = [
            'active',
            'primary_contact',
            'quote',
            'secondary_contact',
            'notes',
            'storage_class_id'
        ]

        # check post data for fields to update
        update = False
        for field in fields:
            if field in post_data:
                old = filesystem.get(field)
                new = post_data.get(field)
                if old != new:
                    if field == 'active':
                        if new == 'on':
                            new = True
                        if new == 'off':
                            new = False
                    filesystem[field] = new
                    update = True

        if update:
            # print(filesystem)
            response = b.bitstore.filesystems().insert(body=filesystem).execute()
            # print(response)

        self.redirect('/admin/filesystems/%s' % (filesystem_id))


class FilesystemPage(webapp2.RequestHandler):
    """Class for FilesystemPage."""

    def get(self, filesystem_id):
        """Return the filesystem page."""
        b = BITStore(**PARAMS)
        filesystem = b.get_filesystem(filesystem_id)
        storageclasses = b.get_storageclasses()
        template = jinja.get_template('admin-filesystem.html')
        body = template.render(
            edit=False,
            filesystem=filesystem,
            fs=filesystem['fs'],
            json=json.dumps(filesystem, indent=2, sort_keys=True),
            storageclasses=sorted(storageclasses, key=lambda x: x['name']),
        )
        output = render_theme(body, self.request)
        self.response.write(output)


class Usage(webapp2.RequestHandler):
    """Class for Usage page."""

    def get(self):
        """Return the usage page."""
        b = BITStore(**PARAMS)
        filesystems = b.get_filesystems()
        storageclasses = b.get_storageclasses()

        # Assemble the filesystem and storage class lists into dicts
        filesys_dict = fs_list_to_dict(filesystems)
        sc_dict = storage_class_list_to_dict(storageclasses)

        date_time = self.request.get('date_time')

        if date_time:
            # Get the data from the supplied date string like 'yy-mm-dd'
            sql_datetime = '(select max(datetime) from broad_bitstore_app.bits_billing_byfs_bitstore_historical where DATE(datetime) = "{}" )'.format(date_time)
            latest_usages = b.get_fs_usages(sql_datetime, memcache=False)
        else:
             # Or else just get the latest usage data from BQ
            sql_datetime = '(select max(datetime) from broad_bitstore_app.bits_billing_byfs_bitstore_historical)'
            latest_usages = b.get_fs_usages(sql_datetime)

        latest_usage_date = latest_usages[1]['datetime'].split("+")[0]

        #print(latest_usages[1])

        # Make the list of dicts into a dict of dicts with fs value as key
        by_fs = {}
        for bq_row in latest_usages:
            # Skip over inactive filesystems for this table
            if not bq_row['active']:
                continue

            # Assign the dictionary fs key to the bq sql result row values
            by_fs[bq_row['fs']] = bq_row

            # Calculate overhead usages as a separate value
            byte_usage_overhead = bq_row.get('byte_usage', 0)
            if not byte_usage_overhead:
                byte_usage_overhead = 0
            byte_usage_without_overhead = bq_row.get('byte_usage_no_overhead', 0)
            # If overhead DOESNT exist, set the overhead usage to 0 and set the share usage to the byte_usage value
            if not byte_usage_without_overhead:
                byte_usage_without_overhead = 0
                by_fs[bq_row['fs']]['share_usage'] = byte_usage_overhead
                overhead_usage = 0
            # If overhead DOES exist, set the overhead usage to usage - overhead and share usage to usage_without_overhead
            else:
                by_fs[bq_row['fs']]['share_usage'] = byte_usage_without_overhead
                overhead_usage = byte_usage_overhead - byte_usage_without_overhead

            by_fs[bq_row['fs']]['overhead_usage'] = overhead_usage

            # Calculate out the total usage value
            dr_byte_usage = by_fs[bq_row['fs']].get('dr_byte_usage', 0)
            if not dr_byte_usage:
                dr_byte_usage = 0
            snapshot_byte_usage = by_fs[bq_row['fs']].get('snapshot_byte_usage', 0)
            if not snapshot_byte_usage:
                snapshot_byte_usage = 0

            total_usage = byte_usage_overhead + snapshot_byte_usage
            by_fs[bq_row['fs']]['total_usage'] = total_usage

            if bq_row['fs'] in filesys_dict:
                if filesys_dict[bq_row['fs']].get('mountpoints'):
                    # This uuuuugly
                    by_fs[bq_row['fs']]['mountpoint'] = filesys_dict[bq_row['fs']]['mountpoints'][0].get('mountpoint')
                if filesys_dict[bq_row['fs']].get('storage_class_id'):
                    fs_sc_id = filesys_dict[bq_row['fs']].get('storage_class_id')
                    if fs_sc_id in sc_dict:
                        by_fs[bq_row['fs']]['storage_class'] = sc_dict[fs_sc_id].get('name')

        available_dates = [todays_date - datetime.timedelta(days=x) for x in range(25)]

        template_values = {
            'filesystems': filesys_dict,
            'by_fs': by_fs,
            'latest_usage_date': latest_usage_date,
            'available_dates': available_dates
        }

        template = jinja.get_template('usage.html')
        body = template.render(template_values)

        output = render_theme(body, self.request)
        self.response.write(output)


class Filesystems(webapp2.RequestHandler):
    """Class for Filesystems page."""

    def get(self):
        """Return the main page."""
        b = BITStore(**PARAMS)
        filesystems = b.get_filesystems()

        servers = {}
        for f in filesystems:
            server = f.get('server', None)
            # Assemble server dictionary
            if server in servers:
                servers[server].append(f)
            else:
                servers[server] = [f]

        template_values = {
            'filesystems': filesystems,
            'servers': servers,
        }
        template = jinja.get_template('admin-filesystems.html')
        body = template.render(template_values)
        output = render_theme(body, self.request)
        self.response.write(output)


app = webapp2.WSGIApplication([
    ('/', Usage),
    #('/admin', AdminPage),
    ('/admin/filesystems', Filesystems),
    (r'/admin/filesystems/(\d+)', FilesystemPage),
    (r'/admin/filesystems/(\d+)/edit', FilesystemEditPage),
], debug=True)
