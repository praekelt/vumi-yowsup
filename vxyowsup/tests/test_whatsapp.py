from twisted.internet.defer import inlineCallbacks

from vumi.tests.utils import LogCatcher
from vumi.tests.helpers import VumiTestCase
from vumi.config import Config
from vumi.errors import ConfigError

from vumi.transports.tests.helpers import TransportHelper

from vxyowsup import WhatsAppTransport


class TestWhatsAppTransport(VumiTestCase):

    def setUp(self):
        self.tx_helper = self.add_helper(TransportHelper(WhatsAppTransport))
        self.config = {
            'cc': '27',
            'phone': '27000000000',
            'password': 'xxxxxxxxxxxxxxxxx=',
        }

        self.transport = yield self.tx_helper.get_transport(self.config)
