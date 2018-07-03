from google.appengine.ext import vendor

# Explicitly add the bitsdb api client, which is a submodule.
vendor.add('bitsdb-api-python-client')

# Add any libraries installed in the `lib` folder.
vendor.add('lib')
