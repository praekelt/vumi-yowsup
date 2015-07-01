import base64

from twisted.internet.defer import inlineCallbacks, DeferredQueue
from twisted.internet import reactor

from vumi.tests.utils import LogCatcher
from vumi.tests.helpers import VumiTestCase
from vumi.config import Config
from vumi.errors import ConfigError

from vumi.transports.tests.helpers import TransportHelper

from vxyowsup.whatsapp import WhatsAppTransport
from yowsup.stacks import YowStackBuilder
from yowsup.layers.logger import YowLoggerLayer
from yowsup.layers import YowLayer
from yowsup.layers.protocol_messages.protocolentities import (
    TextMessageProtocolEntity)


@staticmethod
def getDummyCoreLayers():
    return (TestingLayer, YowLoggerLayer)


def TUMessage_to_PTNode(message):
    # message is TransportUserMessage
    # returns ProtocolTreeNode
    return TextMessageProtocolEntity(message['content'], to=message['to_addr']
                                     + '@s.whatsapp.net').toProtocolTreeNode()


class TestWhatsAppTransport(VumiTestCase):

    @inlineCallbacks
    def setUp(self):
        self.patch(YowStackBuilder, 'getCoreLayers', getDummyCoreLayers)
        self.tx_helper = self.add_helper(TransportHelper(WhatsAppTransport))
        self.config = {
            'cc': '27',
            'phone': '27000000000',
            'password': base64.b64encode("xxx"),
        }

        self.transport = yield self.tx_helper.get_transport(self.config)
        self.testing_layer = self.transport.stack_client.network_layer

    def assert_nodes_equal(self, node1, node2):
        # TODO: test id explicitly
        node2["id"] = node1["id"]
        self.assertEqual(node1.toString(), node2.toString())

    @inlineCallbacks
    def test_outbound(self):
        message_sent = yield self.tx_helper.make_dispatch_outbound(content='fail!', to_addr='double fail!')
        node_received = yield self.testing_layer.data_received.get()
        self.assert_nodes_equal(TUMessage_to_PTNode(message_sent), node_received)


class TestingLayer(YowLayer):

    def __init__(self):
        YowLayer.__init__(self)
        self.data_received = DeferredQueue()

    def onEvent(self, event):
        print("Event at TestingLayer")
        print(event.getName())

    def receive(self, data):
        # data would've been decrypted bytes,
        # but in the testing layer they're yowsup.structs.protocoltreenode.ProtocolTreeNode
        # for convenience
        # receive from lower (no lower in this layer)
        # send to upper
        self.toUpper(data)

    def send(self, data):
        # data is yowsup.structs.protocoltreenode.ProtocolTreeNode
        # receive from upper
        # send to lower (no lower in this layer)
        reactor.callFromThread(self.data_received.put, data)

    def send_to_transport(self, text, to_address):
        # method to be used in testing
        message = TextMessageProtocolEntity(text, to=to_address)
        self.receive(message.toProtocolTreeNode())
