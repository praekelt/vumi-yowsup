import base64
import time

from twisted.internet.defer import inlineCallbacks, DeferredQueue
from twisted.internet import reactor

from vumi.tests.helpers import VumiTestCase
from vumi.message import TransportUserMessage
from vumi.transports.tests.helpers import TransportHelper

from vxyowsup.whatsapp import WhatsAppTransport
from yowsup.stacks import YowStackBuilder
from yowsup.layers.logger import YowLoggerLayer
from yowsup.layers import YowLayer
from yowsup.layers.protocol_messages.protocolentities import TextMessageProtocolEntity
from yowsup.layers.protocol_acks.protocolentities import AckProtocolEntity
from yowsup.layers.protocol_receipts.protocolentities import IncomingReceiptProtocolEntity


@staticmethod
def getDummyCoreLayers():
    return (TestingLayer, YowLoggerLayer)


def TUMessage_to_PTNode(message):
    '''
    message is TransportUserMessage
    returns ProtocolTreeNode
    '''
    return TextMessageProtocolEntity(message['content'], to=message['to_addr']
                                     + '@s.whatsapp.net').toProtocolTreeNode()


def PTNode_to_TUMessage(node):
    '''
    node is ProtocolTreeNode
    returns TransportUserMessage
    '''
    message = TextMessageProtocolEntity.fromProtocolTreeNode(node)
    return TransportUserMessage(to_addr=None, from_addr=message.getFrom(False),
                                content=message.getBody(), transport_name='whatsapp',
                                transport_type='whatsapp')


class TestWhatsAppTransport(VumiTestCase):

    @inlineCallbacks
    def setUp(self):
        self.patch(YowStackBuilder, 'getCoreLayers', getDummyCoreLayers)
        self.tx_helper = self.add_helper(TransportHelper(WhatsAppTransport))
        self.config = {
            'cc': '27',
            'phone': '27000000000',
            'password': base64.b64encode("xxx"),
            #'redis_manager': {'key_prefix': "vumi:whatsapp", 'db': 1},
        }

        self.transport = yield self.tx_helper.get_transport(self.config)
        self.testing_layer = self.transport.stack_client.network_layer

    def assert_id_format_correct(self, node):
        uuid, _sep, count = node["id"].partition('-')
        self.assertEqual(len(uuid), 10)
        self.assertTrue(int(count) > 0)

    def assert_nodes_equal(self, node1, node2):
        self.assert_id_format_correct(node1)
        self.assert_id_format_correct(node2)
        id_stub = node1["id"].split('-')[0]
        xml1 = node1.toString().replace(node1["id"], id_stub)
        xml2 = node2.toString().replace(node2["id"], id_stub)
        self.assertEqual(xml1, xml2)

    def assert_messages_equal(self, message1, message2):
        '''
        assert two instances of TransportUserMessage are equal
        '''
        self.assertEqual(message1['content'], message2['content'])
        self.assertEqual(message1['to_addr'], message2['to_addr'])
        self.assertEqual(message1['from_addr'], message2['from_addr'])

    def assert_ack(self, ack, message, whatsapp_id):
        self.assertEqual(ack.payload['event_type'], 'ack')
        self.assertEqual(ack.payload['user_message_id'], message['message_id'])
        self.assertEqual(ack.payload['sent_message_id'], whatsapp_id)

    def assert_receipt(self, receipt, message):
        self.assertEqual(receipt.payload['event_type'], 'delivery_report')
        self.assertEqual(receipt.payload['user_message_id'], message['message_id'])
        self.assertEqual(receipt.payload['delivery_status'], 'delivered')

    @inlineCallbacks
    def test_outbound(self):
        message_sent = yield self.tx_helper.make_dispatch_outbound(content='fail!', to_addr=self.config.get('phone'), from_addr='vumi')
        node_received = yield self.testing_layer.data_received.get()
        self.assert_nodes_equal(TUMessage_to_PTNode(message_sent), node_received)

        acks = self.tx_helper.get_dispatched_events()
        self.assertFalse(acks)
        self.testing_layer.send_ack(node_received)
        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)

        self.assert_ack(ack, message_sent, node_received['id'])

        self.tx_helper.clear_dispatched_events()

        receipts = self.tx_helper.get_dispatched_events()
        self.assertFalse(receipts)
        self.testing_layer.send_receipt(node_received)
        [receipt] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_receipt(receipt, message_sent)

    @inlineCallbacks
    def test_publish(self):
        message_sent = yield self.testing_layer.send_to_transport(text='Hi Vumi! :)', from_address=self.config.get('phone') + '@s.whatsapp.net')
        [message_received] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_messages_equal(PTNode_to_TUMessage(message_sent), message_received)


class TestingLayer(YowLayer):

    def __init__(self):
        YowLayer.__init__(self)
        self.data_received = DeferredQueue()

    def onEvent(self, event):
        print("Event at TestingLayer")
        print(event.getName())

    def receive(self, data):
        '''
        data would've been decrypted bytes,
        but in the testing layer they're yowsup.structs.protocoltreenode.ProtocolTreeNode
        for convenience
        receive from lower (no lower in this layer)
        send to upper
        '''
        self.toUpper(data)

    def send_ack(self, node):
        # TODO: rather send IncomingAckProtocolEntity and test extra fields
        ack = AckProtocolEntity(_id=node['id'], _class='message')
        self.receive(ack.toProtocolTreeNode())

    def send_receipt(self, node):
        # msg_class=None defualt indicates "delivered"
        # alt: msg_class='read'
        receipt = IncomingReceiptProtocolEntity(_id=node['id'], _from=node['to'], timestamp=str(int(time.time())), type='read')
        self.receive(receipt.toProtocolTreeNode())

    def send(self, data):
        '''
        data is yowsup.structs.protocoltreenode.ProtocolTreeNode
        receive from upper
        send to lower (no lower in this layer)
        '''
        reactor.callFromThread(self.data_received.put, data)

    def send_to_transport(self, text, from_address):
        '''method to be used in testing'''
        message = TextMessageProtocolEntity(text, _from=from_address).toProtocolTreeNode()
        self.receive(message)
        return message
