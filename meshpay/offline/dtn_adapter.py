#!/usr/bin/env python3

from __future__ import annotations

from typing import Any, Dict, Union

from meshpay.types.transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)


PaymentObject = Union[
    TransferOrder,
    SignedTransferOrder,
    ConfirmationOrder,
]


class DTNAdapter:
    """Bridge between MeshPay payment objects and DTN payloads."""

    @staticmethod
    def to_payload(obj: PaymentObject) -> Dict[str, Any]:
        return obj.to_dtn_payload()

    @staticmethod
    def from_payload(payload: Dict[str, Any]) -> PaymentObject:
        payload_type = payload.get("type")

        if payload_type == "transfer_order":
            return TransferOrder.from_dtn_payload(payload)

        if payload_type == "signed_transfer_order":
            return SignedTransferOrder.from_dtn_payload(payload)

        if payload_type == "confirmation_order":
            return ConfirmationOrder.from_dtn_payload(payload)

        raise ValueError(f"unsupported MeshPay offline payload type: {payload_type}")