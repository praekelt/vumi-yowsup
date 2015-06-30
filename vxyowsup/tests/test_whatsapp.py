import base64

from twisted.internet.defer import inlineCallbacks

from vumi.tests.utils import LogCatcher
from vumi.tests.helpers import VumiTestCase
from vumi.config import Config
from vumi.errors import ConfigError

from vumi.transports.tests.helpers import TransportHelper

from vxyowsup.whatsapp import WhatsAppTransport, StackClient
from yowsup.stacks import YowStackBuilder
from yowsup.layers.logger import YowLoggerLayer
from yowsup.layers import YowLayer
from yowsup.layers.protocol_messages.protocolentities import (
    TextMessageProtocolEntity)


class DummyStackBuilder(YowStackBuilder):
    @staticmethod
    def getCoreLayers():
        return (
            YowLoggerLayer,
            TestingLayer,
        )[::-1]


class TestWhatsAppTransport(VumiTestCase):

    @inlineCallbacks
    def setUp(self):
        self.patch(StackClient, 'STACK_BUILDER', DummyStackBuilder)
        self.tx_helper = self.add_helper(TransportHelper(WhatsAppTransport))
        self.config = {
            'cc': '27',
            'phone': '27000000000',
            'password': base64.b64encode("xxx"),
        }

        self.transport = yield self.tx_helper.get_transport(self.config)

    def test_nothing(self):
        pass


class TestingLayer(YowLayer):

    def onEvent(self, event):
        print event

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
        print(data)

    def send_to_transport(self, text, to_address):
        # method to be used in testing
        message = TextMessageProtocolEntity(text, to=to_address)
        self.receive(message.toProtocolTreeNode())
