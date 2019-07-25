#!/usr/bin/env python

"""PubsubMessages

This class deals with all the message queue (PubSub) aspects of the service.
This class is used to both retrieve messages from queues as well as publish
back to queues when results are ready.
"""

from base64 import b64encode, b64decode
import datetime
import json
import logging
import os
import re
import socket
from ssl import SSLError

from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials
from oauth2client.file import Storage

from fh2fc.retry import retry


class PubsubMessages(object):
    """PubsubMessages

    This class contains all the functionality to subscribe (pull) and publish
    to Google PubSub message queues.
    """

    DEFAULT_SCOPES = ["https://www.googleapis.com/auth/pubsub"]
    DEFAULT_CACHE_FILE = "cache.dat"
    DEFAULT_MAX_RETRY = 3
    DEFAULT_SOCKET_TIMEOUT = 10
    DEFAULT_VERSION = "v1"
    MAX_ACKDEADLINE = 600

    # Set the logging name for the script to the class' name
    LOGGER = logging.getLogger(__name__)

    def __init__(
        self,
        service_account_file=None,
        cache_file=None,
        scopes=None,
        project=None,
        max_retry=None,
        socket_timeout=None,
        version=None,
        ackDeadline=None,
    ):
        """Class init method

        Class constructor with several options to setup Pubsub connections as
        well as some other options.

        Args:
            service_account_file (str): The path to the service account JSON
                file.
            cache_file (str): A path to a file that will be used for a cache
                file. This file will hold a cached copy of the access token, so
                make sure it is in a protected area.
            scopes (list): The OAuth2 scopes that the access token should have.
                Defaults to: https://www.googleapis.com/auth/pubsub
            project (str): The name of the Google project in which the PubSub
                to be used resides.
            max_retry (int): The maximum number of times we will retry getting
                a message from PubSub before giving up with an exception.
            socket_timeout (int): The number of seconds we will wait on a TCP
                socket connection to PubSub before determining that there are
                no more messages in the topic.
            version (str): A string representing the version of the API to use.
                Defaults to: "v1"
            ackDeadline (int): The time (in seconds) until a messsage pulled
                from the subscription should expire. The maximum is 600 (10
                minutes)
        """

        # Set defaults
        self.cache_file = self.DEFAULT_CACHE_FILE
        self.scopes = self.DEFAULT_SCOPES
        self.max_retry = self.DEFAULT_MAX_RETRY
        self.socket_timeout = self.DEFAULT_SOCKET_TIMEOUT
        self.version = self.DEFAULT_VERSION
        self.ackDeadline = self.MAX_ACKDEADLINE

        # The top-most key in the dictionary returned from PubSub
        self.top_key = "receivedMessages"

        if not service_account_file:
            raise Exception("Service account file not provided")

        if not os.path.isfile(service_account_file):
            raise Exception("Service account file not found")
        self.service_account_file = service_account_file

        if cache_file:
            self.cache_file = cache_file

        if not project:
            raise Exception("Project name not provided")
        self.project = project

        # A little bit of hackery so we can re-use the fullPath method here
        fp = self.fullPath("project")
        self.project = fp

        # Override the defaults if provided
        if scopes:
            self.scopes = scopes

        if max_retry:
            self.max_retry = max_retry

        if socket_timeout:
            self.socket_timeout = socket_timeout

        if version:
            self.version = version

        if ackDeadline:
            self.ackDeadline = ackDeadline

        # Set the defautl socket timeout to something lower than default
        socket.setdefaulttimeout(self.socket_timeout)

        # Get Google credentials using getCreds
        self.credentials = self.getCreds(
            self.service_account_file,
            self.cache_file,
            self.scopes
        )

        # Create a new pubsub object to use in the rest of the Pub/Sub calls
        self.pubsub = self.getPubsub(
            version=self.version,
            credentials=self.credentials,
            cache_discovery=False
        )

    def getCreds(self, service_account_file, cache_file, scopes):
        """Get Google credentials from a service account file

        Using the provided `service_account_file`, an access token is retrieved
        from Google with the requested `scopes`.  To prevent frequent calls to
        Google to get new tokens, a cache is used to hold the access token so
        that multiple runs will use the cached token if it is still valid.

        Args:
            service_account_file (str): The path to the service account JSON
                file.
            cache_file (str): A path to a file that will be used for a cache
                file. This file will hold a cached copy of the access token, so
                make sure it is in a protected area.
            scopes (list): The OAuth2 scopes that the access token should have.
                Defaults to: https://www.googleapis.com/auth/pubsub
        """

        # Setup storage to cache the access token to a file.  This saves us
        # having to do the full OAuth2 handshake every time this script is run
        # if we have a valid token in storage already.
        storage = Storage(cache_file)
        # If we don't have a cache file already, create one.
        storage._create_file_if_needed()
        self.credentials = storage.get()

        # Get a new access token if the cache is missing, old or invalid.
        if self.credentials is None or self.credentials.invalid:
            self.LOGGER.debug(
                "getCreds: credential cache is invalid.  Getting new token."
            )
            # We're using a service account, so get the data from a JSON file.
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                service_account_file,
                scopes=scopes
            )
            # Make sure we have the access token before we cache
            creds.get_access_token()

            # Save the credentials in storage to be used in subsequent runs.
            storage.put(creds)
            creds.set_store(storage)
            self.credentials = creds
        else:
            self.LOGGER.debug(
                "getCreds: cached credentials are valid and will be used"
            )

        self.LOGGER.debug("getCreds: credentials retrieved")

        return self.credentials

    def getPubsub(
        self,
        version="v1",
        credentials=None,
        cache_discovery=False
    ):
        """Use the discovery service to build a pubsub client

        Using the provided `credentials`, use the `build` discovery method to
        create a "pubsub" object to be used for all further API calls to
        PubSub

        Args:
            version (str): A string representing the version of the API to use.
                Defaults to: "v1"
            credentials (obj): An oauth2client credentials object that has
                already been properly created
            cache_discovery (bool): Turn on or off caching of discovery data
        """

        # Create a new pubsub object to use in the rest of the Pub/Sub calls
        self.pubsub = build(
            "pubsub",
            version,
            credentials=credentials,
            cache_discovery=cache_discovery
        )

        return self.pubsub

    def verifyTopics(self, topics=None):
        """Verify the topics in a project

        For a given `project`, cycle through all the available topics and
        return **False** if any of the `topics` provided do not exist.

        Args:
            topics (list): The list of topics one expects to be in the project.
        """

        if not topics:
            raise Exception("No topic(s) provided for verification")

        # Get the list of all topics in the current project.
        ret = self.pubsub.projects().topics().list(
            project=self.project
        ).execute()

        if "topics" not in ret:
            self.LOGGER.debug(
                "verifyTopics: no topics found in the current project"
            )
            return False

        # Get the names of the topics from the "name" attribute
        existingTopics = []
        for t in ret["topics"]:
            if "name" not in t:
                continue
            self.LOGGER.debug("verifyTopics: found topic %s" % t["name"])
            existingTopics.append(t["name"])

        # Cycle through the list of mandatory topics and return False if any of
        # the mandatory topics are missing.
        for t in topics:
            if t not in existingTopics:
                self.LOGGER.error(
                    "verifyTopics: could not find topic '%s' in list" % t
                )
                return False
            else:
                self.LOGGER.debug("verifyTopics: topic '%s' verified" % t)

        return True

    def verifySubscriptions(self, subscriptions=None):
        """Verify the subscriptions in a project

        For a given `project`, cycle through all the available subscriptions
        and return **False** if any of the `subscriptions` provided do not
        exist.

        Args:
            subscriptions (list): The list of subscriptions one expects to be
                in the project.
        """

        if not subscriptions:
            raise Exception("No subscription(s) provided for verification")

        # Get the list of all subscriptions in the current project.
        ret = self.pubsub.projects().subscriptions().list(
            project=self.project
        ).execute()

        if "subscriptions" not in ret:
            self.LOGGER.debug(
                "verifySubscriptions: no subscriptions found in the current"
                " project"
            )
            return False

        # Get the names of the subscriptions from the "name" attribute
        existingSubs = []
        for s in ret["subscriptions"]:
            if "name" not in s:
                continue
            self.LOGGER.debug(
                "verifySubscriptions: found subscription %s" % s["name"]
            )
            existingSubs.append(s["name"])

        # Cycle through the list of mandatory subscriptions and return False if
        # any of the mandatory subscriptions are missing.
        for s in subscriptions:
            if s not in existingSubs:
                self.LOGGER.error(
                    "verifySubscriptions: could not find subscription '%s' "
                    "in list" % s
                )
                return False
            else:
                self.LOGGER.debug(
                    "verifySubscriptions: subscription '%s' verified" % s
                )

        return True

    def createTopic(self, topic=None):
        """Create the specified topic in a project

        For the project defined for this object, create the `topic` specified.

        If the topic already exists, just return **True**

        Args:
            topic (str): The topic to create.  The topic must be in the proper
                "projects/{project}/topics/{topic}" format.
        """

        if not topic:
            raise Exception("No topic provided for creation")

        # Fill in the necessary data in the body of the request
        body = {}

        if self.verifyTopics([topic]):
            self.LOGGER.debug("createTopic: topic '%s' found" % topic)
            return True

        # Create the topic.
        self.pubsub.projects().topics().create(name=topic, body=body).execute()

        self.LOGGER.debug("createTopic: topic '%s' created" % topic)

        return True

    def createSubscription(
        self,
        subscription=None,
        topic=None,
        ackDeadline=None,
    ):
        """Create the specified subscription in a project

        For the project defined for this object, create the `subscription`
        for the `topic` specified.  You can also specify a modified
        acknowledgement deadline (in seconds) as well.

        If the subscription already exists, just return **True**

        Args:
            subscription (str): The subscription to create.  The subscription
                must be in the proper
                "projects/{project}/subscriptions/{subscription}" format.
            topic (str): The topic to create.  The topic must be in the proper
                "projects/{project}/topics/{topic}" format.
            ackDeadline (int): The time in seconds that a message pulled from
                PubSub will wait for an acknowledgement before being labelled
                as abandoned and given out to another subscriber.
                Default: deadline used in the constructor
        """

        if not subscription:
            raise Exception("No subscription provided for creation")

        if not topic:
            raise Exception("Cannot subscribe without a topic")

        if self.verifySubscriptions([subscription]):
            self.LOGGER.debug("createSubscription: subscription '%s' found" % (
                subscription
            ))
            return True

        new_deadline = self.ackDeadline
        if ackDeadline:
            new_deadline = ackDeadline

        # Fill in the necessary data in the body of the request
        body = {
            "topic": topic,
            "ackDeadlineSeconds": new_deadline,
        }

        # Create the topic.
        self.pubsub.projects().subscriptions().create(
            name=subscription,
            body=body,
        ).execute()

        self.LOGGER.debug("createSubscription: subscription '%s' created" % (
            subscription
        ))

        return True

    def messagePull(self, subscription=None):
        """Pull a message from PubSub

        This method will pull the next message it can from the `subscription`
        provided.

        Args:
            subscription (str): The subscription from which we will pull the
                message.  The subscription must be in the proper
                "projects/{project}/subscriptions/{subscription}" format.

        Returns:
            (dict) The raw retrieved message in dict format.
        """
        msg = None
        expire = None

        # Throw an exception if a subscription isn't provided
        if not subscription:
            raise Exception("No subcription provided to messagePull")

        # You need to at least specify maxMessages when calling the Pub/Sub
        # API.
        params = {
            "maxMessages": 1,
            "returnImmediately": True,
        }

        tries = 0
        # Retry pulling a message as sometimes pulling a message times out.
        while(True):
            tries += 1
            # Hard-stop at internally-set max_retry
            if tries > self.max_retry:
                raise Exception("Too many retries trying to pull message.")

            # Pull one or more messages from the queue
            try:
                msg = self.pubsub.projects().subscriptions().pull(
                    subscription=subscription,
                    body=params
                ).execute()

                # Expire at ackDeadline minus 30 seconds to give us a buffer
                # unless ackDeadline is less than 30 seconds, in which case we
                # just use the ackDeadline
                if self.ackDeadline < 30:
                    expiresecs = self.ackDeadline
                else:
                    expiresecs = self.ackDeadline - 30

                # Figure out when this message will expire
                now = datetime.datetime.now()
                expire = now + datetime.timedelta(seconds=expiresecs)
            except SSLError as e:
                # SSLError exception can be a timeout, so retry
                self.LOGGER.error(e, exc_info=True)
                continue
            # This exception turns out not to be needed. Leaving it in the
            # code in case we ever run across a rash of network timeouts that
            # keep causing this error from deep in the network stack.
            # except socket.timeout as e:
                # raise Exception("No more messages in the queue")
            else:
                break

        # If the message is empty (timeout, error, etc.), throw an exception
        if not msg:
            self.LOGGER.debug("messagePull: %s" % msg)
            raise PubsubMessagesEmptyQueue(
                "Empty message received. No more messages in the queue"
            )

        self.LOGGER.info(
            "messagePull: message pulled from the queue: %s [%s]" % (
                msg["receivedMessages"][0]["message"]["messageId"],
                msg["receivedMessages"][0]["message"]["publishTime"],
            )
        )
        msg["receivedMessages"][0]["expireTime"] = expire
        self.LOGGER.debug("messagePull: %s" % msg)

        return msg

    @retry(Exception)
    def messageSend(self, topic=None, attr={}, data={}):
        """Send a message to PubSub

        This method will send the provided `message` to the `topic` provided.

        Args:
            topic (str): The topic to which we will send the message.  The
                topic must be in the proper
                "projects/{project}/topics/{topic}" format.
            attr (dict): The attributes to send in the message
                Default: **empty**
            data (dict): The data to send in the message.  The data is expected
                to be a dictionary.
                Default: **empty**

        Returns:
            (dict) If successful, the return value from publishing to PubSub is
                returned (as a dictionary).
        """

        # Throw an exception if a topic isn't provided
        if not topic:
            raise Exception("No topic provided to messageSend")

        # Data or attributes (or both) are required for the current application
        if not data and not attr:
            raise Exception("No data or attributes provided to messageSend")

        message = {}
        message["messages"] = []
        message["messages"].append({})

        # If attributes are provided, make sure it is added in the correct
        # format
        if attr:
            message["messages"][0]["attributes"] = attr

        # If data is provided, make sure it is added in the correct format
        if data:
            data = json.dumps(data, sort_keys=True)
            data = data.encode("utf-8")
            data = b64encode(data)
            message["messages"][0]["data"] = data.decode("utf-8")

        ret = self.pubsub.projects().topics().publish(
            topic=topic,
            body=message
        ).execute()

        self.LOGGER.info("messageSend: message sent")
        self.LOGGER.debug("messageSend: %s" % ret)

        return ret

    def messageAck(self, subscription=None, ackId=None):
        """Send an acknowledgement to PubSub

        This method will send an acknowledgement for the specific
        acknowledgement ID (`ackId`) to the `subscription` provided.

        Args:
            subscription (str): The subscription to which we will send the
                acknowledgement.  The subscription must be in the proper
                "projects/{project}/subscriptions/{subscription}" format.
            attr (dict): The attributes to send in the message
                Default: **empty**
            data (dict): The data to send in the message
                Default: **empty**

        Returns:
            (dict) If successful, the message sent to PubSub is returned as a
                dictionary.
        """

        # Throw an exception if a subscription isn't provided
        if not subscription:
            raise Exception("No subscription provided to messageAck")

        # The acknowledgement ID we are using to acknowledge a message
        if not ackId:
            raise Exception("No acknowledgement ID provided to messageAck")

        # ackId must be a list
        if not isinstance(ackId, list):
            ackId = [ackId]

        # Fill in the necessary data in the body of the request
        body = {
            "ackIds": ackId
        }

        # acknowledge returns no data, so don't capture any results
        self.pubsub.projects().subscriptions().acknowledge(
            subscription=subscription,
            body=body
        ).execute()

        self.LOGGER.info("messageAck: message acknowledged")
        self.LOGGER.debug("messageAck: %s" % ackId)

    def requeueMessage(self, subscription=None, topic=None):
        """Pull then requeue a single message to the PubSub topic

        This function will pull one message from the specified subscription.
        It will then immediately requeue the message back to the topic
        specified.  Once the requeue is successful, it will acknowledge the
        original message so there are no duplicates.

        If a message is found, this method will return True.  If no messages
        are found in the queue, the method will return False.

        Args:
            subscription (str): The PubSub subscription name
            topic (str): The PubSub topic name

        Returns:
            (boolean) True on message found and requeued, False otherwise
        """

        if not subscription:
            raise Exception("No subcription provided to requeueMessage")

        if not topic:
            raise Exception("No topic provided to requeueMessage")

        # Pull a message from the PubSub topic
        try:
            msg = self.messagePull(subscription)
        except PubsubMessagesEmptyQueue as e:
            # If we get this exception it means there are no messages in the
            # subscription to process
            return False

        # Get the acknowledgement ID so we can acknowledge this message
        ackId = self.getMessageAckId(msg)

        # Validate the message.  Exception will be raised if invalid.
        self.validateMessage(msg)

        # Get a list of attributes from the message, because all messages in
        # this system should have attributes.
        try:
            attr = self.getMessageAttributes(message=msg)
            data = self.getMessageData(message=msg)
        except Exception as e:
            msgId = msg["receivedMessages"][0]["message"]["messageId"]
            self.LOGGER.error(
                "Message '%s' is corrupted.  Dropping message." % msgId
            )
            self.LOGGER.error("requeueMessage: %s" % str(e))

            # Acknowledge the corrupt message so no one else gets it.
            self.messageAck(subscription, ackId)

            # Re-raise the original exception to alert the caller that this
            # function has indeed failed.
            raise e

        self.LOGGER.debug("requeueMessage: %s" % attr)
        self.LOGGER.debug("requeueMessage: %s" % data)

        # Send a message back to the topic
        self.messageSend(topic=topic, attr=attr, data=data)

        # If we make it this far, acknowledge the message
        self.messageAck(subscription, ackId)

        return True

    def validateMessage(self, message={}):
        """Tests the dictionary returned by PubSub to see if it's valid

        This method will check the basic dictionary structure of a message
        returned from PubSub to make sure it's correct.

        Args:
            message (dict): The PubSub message
                Default: **empty**

        Returns:
            (boolean) True, raises exception on error
        """

        # Do not allow empty messages
        if not message:
            raise Exception("Message cannot be empty")

        # Test various keys in the array to make sure the data is correct.
        if self.top_key not in message.keys():
            raise Exception("%s key not found" % self.top_key)

        if not isinstance(message[self.top_key], list):
            raise Exception("List of messages not found")

        if len(message[self.top_key]) < 1:
            raise Exception("List of messages is empty")

        if not isinstance(message[self.top_key][0], dict):
            raise Exception("Message contents not found")

        if "ackId" not in message[self.top_key][0].keys():
            raise Exception("ackId key not found")

        if "message" not in message[self.top_key][0].keys():
            raise Exception("message key not found")

        if "data" not in message[self.top_key][0]["message"].keys():
            raise Exception("data key not found")

        return True

    def getMessageAckId(self, message={}):
        """Retrieve a message's acknowledgement ID from a PubSub message

        This method will extract the acknowledgement ID (`ackId`) from a
        message represented by the `message` dictionary passed to this method.

        Args:
            message (dict): The PubSub message
                Default: **empty**

        Returns:
            (string) If successful, the message ackId is returned
        """

        self.validateMessage(message)

        ackId = str(message[self.top_key][0]["ackId"])

        self.LOGGER.info(
            "getMessageAckId: message acknowledgement ID retrieved"
        )
        self.LOGGER.debug("getMessageAckId: %s" % ackId)

        # Return the ackId field
        return ackId

    def getMessageAttributes(self, message={}, required=[]):
        """Retrieve a message's attributes from a PubSub message

        This method will extract the `attributes` field from a message
        represented by the `message` dictionary passed to this method.  An
        optional list of required attributes can also be passed to this method
        to make sure the attribute list is complete.

        Args:
            message (dict): The PubSub message
                Default: **empty**
            required (list): A list of required attribute names that must exist
                in the message.  If any of the required attributes do not
                exist, this method will throw an exception.

        Returns:
            (dict) If successful, a dictionary of attributes is returned
        """

        self.validateMessage(message)

        # Return empty if no attributes are included in the message
        if "attributes" not in message[self.top_key][0]["message"]:
            return {}

        attr = message[self.top_key][0]["message"]["attributes"]

        self.LOGGER.info("getMessageAttributes: message attributes retrieved")
        self.LOGGER.debug("getMessageAttributes: %s" % attr)

        if not isinstance(required, list):
            required = [required]

        if required:
            for r in required:
                if r not in attr.keys():
                    raise Exception(
                        "attribute '%s' not found in message" % r
                    )

        # Return the attributes from the message
        return attr

    def getMessageData(self, message={}, required=[]):
        """Retrieve a message's data from a PubSub message

        This method will extract the `data` field from a message represented by
        the `message` dictionary passed to this method.  After `data` is
        retrieved, it will be base64 decoded and json decoded so a dictionary
        can be returned.  An optional list of required keys can also be passed
        to this method to make sure the key list is complete.

        Args:
            message (dict): The PubSub message
                Default: **empty**
            required (list): A list of required key names that must exist in
                the data.  If any of the required attributes do not exist, this
                method will throw an exception.

        Returns:
            (dict) If successful, a dictionary of attributes is returned
        """

        self.validateMessage(message)

        enc_data = message[self.top_key][0]["message"]["data"]

        self.LOGGER.info("getMessageData: message data retrieved")

        # First, base64 decode the message
        tmp = b64decode(enc_data)
        # Change it from bytes back to a string
        tmp = tmp.decode('utf-8')

        # Decode JSON to a dictionary
        try:
            data = json.loads(tmp)
        except ValueError:
            raise Exception("data in message is not in JSON format")

        self.LOGGER.debug("getMessageData: %s" % data)

        if not isinstance(required, list):
            required = [required]

        if required:
            for r in required:
                if r not in data.keys():
                    raise Exception(
                        "key '%s' not found in message" % r
                    )

        # Return the attributes from the message
        return data

    def getMessageExpireTime(self, message={}):
        """Retrieve a message's expire time

        This method will extract the expire time (`expireTime`) from a
        message represented by the `message` dictionary passed to this method.

        Args:
            message (dict): The PubSub message
                Default: **empty**

        Returns:
            (string) If successful, the message expireTime is returned
        """

        expireTime = message[self.top_key][0]["expireTime"]

        self.LOGGER.info("getMessageExpireTime: message expire time retrieved")
        self.LOGGER.debug("getMessageExpireTime: %s" % str(expireTime))

        # Return the expireTime field
        return expireTime

    def fullPath(self, ps_type=None, data=None):
        """Build a full PubSub compatible path

        PubSub uses a long format to designate projects, topics, and
        subscriptions.  This method builds the full path depending on the
        passed `ps_type` (topic, subscription) and the `data` (short topic
        or subscription names).

        Args:
            ps_type (str): The type of pubsub path you're creating.  Valid
                values are "project", "topic" or "subscription".
                Default: **empty**
            data (list): A list of topics or subscriptions to turn into full
                paths.  You can also pass a string and it will function
                correctly as well.  If you don't pass a value, this method will
                throw an exception, unless the `ps_type` is project.  If
                `ps_type` is "project", it will just return the full project
                path.

        Returns:
            (list) If the list lengths is more than one element, a list of
            full paths based on the original list of short paths is returned.
            If there is only one element in the list, a string is returned with
            the full path representation of the provided short path.
        """

        # The default prefix for Pub/Sub projects
        prefix = "projects/"
        valid_types = ["project", "subscriptions", "topics"]

        if ps_type not in valid_types:
            raise Exception("Invalid type provided")

        # If the prefix is already on project, don't add a second prefix
        if re.search(r"^projects/", self.project):
            prefix = ""

        self.LOGGER.debug("fullPath: prefix: %s" % prefix)

        # The data parameter shouldn't be empty...
        if not data:
            # ...unless we're just building the name of the project itself.
            if ps_type is "project":
                self.LOGGER.debug("fullPath: full paths generated")
                return "%s%s" % (prefix, self.project)
            else:
                # Throw an exception for all other path types.
                raise Exception("data not provided for type %s" % ps_type)

        # Make data into a list if it isn't one already
        if not isinstance(data, list):
            data = [data]

        # Create a new list of paths in the correct format
        paths = []
        for d in data:
            p = "%s%s/%s/%s" % (prefix, self.project, ps_type, d)
            self.LOGGER.debug("fullPath: full path built: %s" % p)
            paths.append(p)

        # If the list is only 1 element long, turn it back into a string
        if len(paths) == 1:
            paths = paths[0]

        self.LOGGER.debug("fullPath: full paths generated")

        return paths


class PubsubMessagesEmptyQueue(Exception):
    """PubsubMessagesEmptyQueue

    This is a custom exception for the PubsubMessages class that can be raised
    when there are no more messages in the queue.
    """
