from types import SimpleNamespace

from meshpay.oppnet.interfaces import get_interface_profile, supported_wireless_interfaces
from meshpay.policy import build_default_policy, default_authority_keys, sign_policy
from meshpay.routing.registry import create_routing_protocol, supported_routing_algorithms
from meshpay.routing.sdn import SDNDTNRouting
from meshpay.types import Address, NodeType
from meshpay.types.transaction import MessageBufferItem


def test_routing_registry_resolves_supported_algorithms():
    for name in supported_routing_algorithms():
        protocol = create_routing_protocol("node1", name)
        assert protocol.node_id == "node1"


def test_wireless_interface_registry_resolves_profiles():
    for name in supported_wireless_interfaces():
        profile = get_interface_profile(name)
        assert profile.name == name
        assert profile.link_class() is not None


def test_sdn_dtn_falls_back_to_epidemic_without_active_policy():
    node = SimpleNamespace(
        name="user1",
        address=Address("user1", "10.0.0.1", 9001, NodeType.CLIENT),
        params={},
        state=SimpleNamespace(neighbors={}),
        message_buffer={},
    )
    protocol = SDNDTNRouting("user1")
    protocol.set_node(node)
    buffer = {
        "msg1": MessageBufferItem(
            message_id="msg1",
            message_type="transfer_request",
            payload={},
            sender_id="user1",
            ttl=8,
        )
    }

    protocol.on_neighbor_discovered("peer1", buffer)
    outbox = protocol.get_messages_to_send()

    assert outbox
    assert outbox[0]["payload"]["protocol_type"] == "epidemic_summary"


def test_sdn_dtn_uses_active_policy_forwarding_rules():
    authorities = ["auth1", "auth2", "auth3", "auth4", "auth5"]
    neighbors = {
        authority: Address(authority, f"10.0.0.{idx}", 8000 + idx, NodeType.AUTHORITY)
        for idx, authority in enumerate(authorities, start=1)
    }
    node = SimpleNamespace(
        name="user1",
        address=Address("user1", "10.0.0.20", 9020, NodeType.CLIENT),
        params={},
        state=SimpleNamespace(neighbors=neighbors),
        message_buffer={},
    )
    protocol = SDNDTNRouting("user1")
    protocol.set_node(node)

    policy = build_default_policy(authorities, epoch=1, valid_from=0, valid_until=4102444800)
    keys = default_authority_keys(authorities)
    for authority in authorities[:4]:
        result = protocol.policy_store.add_policy_fragment(sign_policy(policy, authority, keys[authority]))
    assert result.active

    item = MessageBufferItem(
        message_id="order1",
        message_type="transfer_request",
        payload={"transfer_order": {"order_id": "order1", "sender": "user1", "recipient": "user2"}},
        sender_id="user1",
        ttl=8,
    )
    node.message_buffer = {"order1": item}
    protocol.on_message_added_to_buffer("order1", node.message_buffer)
    outbox = protocol.get_messages_to_send()

    relay = [entry for entry in outbox if entry["type"] == "relay"]
    assert len(relay) == 5
    assert relay[0]["interface_preference"] == ["mesh_80211s", "wifi_direct", "wwan_d2d"]
