from twisted.internet.defer import inlineCallbacks

from vumi.tests.utils import LogCatcher
from vumi.tests.helpers import VumiTestCase
from vumi.config import Config
from vumi.errors import ConfigError

from vumi.transports.tests.helpers import TransportHelper

from vxyowsup import WhatsAppTransport, WhatsAppInterface
from yowsup.stacks import YowStack, YowStackBuilder
from yowsup.layers.logger import YowLoggerLayer
from yowsup.layers.__init__ import YowLayer
from yowsup.layers import YowParallelLayer


class TestWhatsAppTransport(VumiTestCase):

    def setUp(self):
        self.tx_helper = self.add_helper(TransportHelper(WhatsAppTransport))
        self.config = {
            'cc': '27',
            'phone': '27000000000',
            'password': 'xxxxxxxxxxxxxxxxx=',
        }

        self.transport = yield self.tx_helper.get_transport(self.config)

        # true core layers:
        # (
        #     YowLoggerLayer,
        #     YowCoderLayer,
        #     YowCryptLayer,
        #     YowStanzaRegulator,
        #     YowNetworkLayer
        # )[::-1]

        # YowCoderLayer decodes bytearrays into instances of ProtocolTreeNode
        # falsify this layer and all those below it into TestingLayer

        new_core_layers = (YowLoggerLayer, TestingLayer)[::-1]

        new_layers = new_core_layers + \
            YowParallelLayer(
                YowStackBuilder.getProtocolLayers(),) + (WhatsAppInterface,)

        new_stack = YowStack(new_layers, reversed=False)

        self.stack.setCredentials(self.transport.stack_client.CREDENTIALS)
        self.patch(
            self.transport.stack_client, self.transport.stack_client.stack, new_stack)

#    def test_handle_outbound(self):
#    	message = self.tx_helper.make_outbound('blep', from_addr='27xxxxxxxxx', to_addr='27xxxxxxxxxxx')


class TestingLayer(YowLayer):
    pass
