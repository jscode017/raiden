# pylint: disable=too-many-arguments
import random
import string
from dataclasses import dataclass, fields, replace
from functools import singledispatch
from typing import Any, ClassVar, Dict, List, NamedTuple, Optional, Tuple, Type

from eth_utils import to_checksum_address

from raiden.constants import EMPTY_MERKLE_ROOT, UINT64_MAX, UINT256_MAX
from raiden.messages import Lock, LockedTransfer
from raiden.transfer import balance_proof, channel, token_network
from raiden.transfer.identifiers import CanonicalIdentifier
from raiden.transfer.mediated_transfer import mediator
from raiden.transfer.mediated_transfer.state import (
    HashTimeLockState,
    LockedTransferSignedState,
    LockedTransferUnsignedState,
    MediationPairState,
    TransferDescriptionWithSecretState,
    lockedtransfersigned_from_message,
)
from raiden.transfer.mediated_transfer.state_change import ActionInitMediator
from raiden.transfer.merkle_tree import compute_layers, merkleroot
from raiden.transfer.state import (
    NODE_NETWORK_REACHABLE,
    BalanceProofSignedState,
    BalanceProofUnsignedState,
    MerkleTreeState,
    NettingChannelEndState,
    NettingChannelState,
    RouteState,
    TokenNetworkState,
    TransactionExecutionStatus,
    message_identifier_from_prng,
)
from raiden.transfer.state_change import ContractReceiveChannelNew, ContractReceiveRouteNew
from raiden.transfer.utils import hash_balance_data
from raiden.utils import privatekey_to_address, random_secret, sha3, typing
from raiden.utils.signer import LocalSigner, Signer

EMPTY = "empty"
GENERATE = "generate"


def _partial_dict(full_dict: Dict, *args) -> Dict:
    return {key: full_dict[key] for key in args}


class Properties:
    """ Base class for all properties classes. """

    DEFAULTS: ClassVar["Properties"] = None
    TARGET_TYPE: ClassVar[Type] = None

    @property
    def kwargs(self):
        return {key: value for key, value in self.__dict__.items() if value is not EMPTY}

    def extract(self, subset_type: Type) -> "Properties":
        field_names = [field.name for field in fields(subset_type)]
        return subset_type(**_partial_dict(self.__dict__, *field_names))

    def partial_dict(self, *args) -> Dict[str, Any]:
        return _partial_dict(self.__dict__, *args)


def if_empty(value, default):
    return value if value is not EMPTY else default


def _replace_properties(properties, defaults):
    replacements = {
        k: create_properties(v, defaults.__dict__[k]) if isinstance(v, Properties) else v
        for k, v in properties.kwargs.items()
    }
    return replace(defaults, **replacements)


def create_properties(properties: Properties, defaults: Properties = None) -> Properties:
    full_defaults = type(properties).DEFAULTS
    if defaults is not None:
        full_defaults = _replace_properties(defaults, full_defaults)
    return _replace_properties(properties, full_defaults)


def make_uint256() -> int:
    return random.randint(0, UINT256_MAX)


def make_channel_identifier() -> typing.ChannelID:
    return typing.ChannelID(make_uint256())


def make_uint64() -> int:
    return random.randint(0, UINT64_MAX)


def make_balance() -> typing.Balance:
    return typing.Balance(random.randint(0, UINT256_MAX))


def make_block_number() -> typing.BlockNumber:
    return typing.BlockNumber(random.randint(0, UINT256_MAX))


def make_chain_id() -> typing.ChainID:
    return typing.ChainID(random.randint(0, UINT64_MAX))


def make_message_identifier() -> typing.MessageID:
    return typing.MessageID(random.randint(0, UINT64_MAX))


def make_20bytes() -> bytes:
    return bytes("".join(random.choice(string.printable) for _ in range(20)), encoding="utf-8")


def make_address() -> typing.Address:
    return typing.Address(make_20bytes())


def make_checksum_address() -> str:
    return to_checksum_address(make_address())


def make_32bytes() -> bytes:
    return bytes("".join(random.choice(string.printable) for _ in range(32)), encoding="utf-8")


def make_transaction_hash() -> typing.TransactionHash:
    return typing.TransactionHash(make_32bytes())


def make_block_hash() -> typing.BlockHash:
    return typing.BlockHash(make_32bytes())


def make_privatekey_bin() -> bin:
    return make_32bytes()


def make_payment_network_identifier() -> typing.PaymentNetworkID:
    return typing.PaymentNetworkID(make_address())


def make_keccak_hash() -> typing.Keccak256:
    return typing.Keccak256(make_32bytes())


def make_secret(i: int = EMPTY) -> bytes:
    if i is not EMPTY:
        return format(i, ">032").encode()
    else:
        return make_32bytes()


def make_privatekey(privatekey_bin: bytes = EMPTY) -> bytes:
    return if_empty(privatekey_bin, make_privatekey_bin())


def make_privatekey_address(privatekey: bytes = EMPTY,) -> Tuple[bytes, typing.Address]:
    privatekey = if_empty(privatekey, make_privatekey())
    address = privatekey_to_address(privatekey)
    return privatekey, address


def make_signer(privatekey: bytes = EMPTY) -> Signer:
    privatekey = if_empty(privatekey, make_privatekey())
    return LocalSigner(privatekey)


def make_route_from_channel(channel_state: NettingChannelState = EMPTY) -> RouteState:
    channel_state = if_empty(channel_state, create(NettingChannelStateProperties()))
    return RouteState(channel_state.partner_state.address, channel_state.identifier)


def make_channel_endstate(
    address: typing.Address = EMPTY, balance: typing.Balance = EMPTY
) -> NettingChannelEndState:
    address = if_empty(address, make_address())
    balance = if_empty(balance, 0)
    return NettingChannelEndState(address, balance)


def make_channel_state(
    our_balance: typing.Balance = EMPTY,
    partner_balance: typing.Balance = EMPTY,
    our_address: typing.Address = EMPTY,
    partner_address: typing.Address = EMPTY,
    token_address: typing.TokenAddress = EMPTY,
    payment_network_identifier: typing.PaymentNetworkID = EMPTY,
    token_network_identifier: typing.TokenNetworkID = EMPTY,
    channel_identifier: typing.ChannelID = EMPTY,
    reveal_timeout: typing.BlockTimeout = EMPTY,
    settle_timeout: int = EMPTY,
) -> NettingChannelState:

    our_balance = if_empty(our_balance, 0)
    partner_balance = if_empty(partner_balance, 0)
    our_address = if_empty(our_address, make_address())
    partner_address = if_empty(partner_address, make_address())
    token_address = if_empty(token_address, make_address())
    payment_network_identifier = if_empty(
        payment_network_identifier, make_payment_network_identifier()
    )
    token_network_identifier = if_empty(token_network_identifier, make_address())
    channel_identifier = if_empty(channel_identifier, make_channel_identifier())
    reveal_timeout = if_empty(reveal_timeout, UNIT_REVEAL_TIMEOUT)
    settle_timeout = if_empty(settle_timeout, UNIT_SETTLE_TIMEOUT)

    opened_block_number = 10
    close_transaction: TransactionExecutionStatus = None
    settle_transaction: TransactionExecutionStatus = None
    our_state = make_channel_endstate(address=our_address, balance=our_balance)
    partner_state = make_channel_endstate(address=partner_address, balance=partner_balance)
    open_transaction = TransactionExecutionStatus(
        started_block_number=None,
        finished_block_number=opened_block_number,
        result=TransactionExecutionStatus.SUCCESS,
    )

    return NettingChannelState(
        canonical_identifier=make_canonical_identifier(
            token_network_address=token_network_identifier, channel_identifier=channel_identifier
        ),
        token_address=token_address,
        payment_network_identifier=payment_network_identifier,
        reveal_timeout=reveal_timeout,
        settle_timeout=settle_timeout,
        mediation_fee=0,
        our_state=our_state,
        partner_state=partner_state,
        open_transaction=open_transaction,
        close_transaction=close_transaction,
        settle_transaction=settle_transaction,
    )


def make_transfer_description(
    payment_network_identifier: typing.PaymentNetworkID = EMPTY,
    payment_identifier: typing.PaymentID = EMPTY,
    amount: typing.TokenAmount = EMPTY,
    token_network: typing.TokenNetworkID = EMPTY,
    initiator: typing.InitiatorAddress = EMPTY,
    target: typing.TargetAddress = EMPTY,
    secret: typing.Secret = EMPTY,
    allocated_fee: typing.FeeAmount = EMPTY,
) -> TransferDescriptionWithSecretState:
    payment_network_identifier = if_empty(
        payment_network_identifier, UNIT_PAYMENT_NETWORK_IDENTIFIER
    )
    payment_identifier = if_empty(payment_identifier, UNIT_TRANSFER_IDENTIFIER)
    amount = if_empty(amount, UNIT_TRANSFER_AMOUNT)
    token_network = if_empty(token_network, UNIT_TOKEN_NETWORK_ADDRESS)
    initiator = if_empty(initiator, UNIT_TRANSFER_INITIATOR)
    target = if_empty(target, UNIT_TRANSFER_TARGET)
    secret = if_empty(secret, random_secret())
    allocated_fee = if_empty(allocated_fee, 0)

    return TransferDescriptionWithSecretState(
        payment_network_identifier=payment_network_identifier,
        payment_identifier=payment_identifier,
        amount=amount,
        allocated_fee=allocated_fee,
        token_network_identifier=token_network,
        initiator=initiator,
        target=target,
        secret=secret,
    )


def make_signed_transfer(
    amount: typing.TokenAmount = EMPTY,
    initiator: typing.InitiatorAddress = EMPTY,
    target: typing.TargetAddress = EMPTY,
    expiration: typing.BlockExpiration = EMPTY,
    secret: typing.Secret = EMPTY,
    payment_identifier: typing.PaymentID = EMPTY,
    message_identifier: typing.MessageID = EMPTY,
    nonce: typing.Nonce = EMPTY,
    transferred_amount: typing.TokenAmount = EMPTY,
    locked_amount: typing.TokenAmount = EMPTY,
    locksroot: typing.Locksroot = EMPTY,
    recipient: typing.Address = EMPTY,
    channel_identifier: typing.ChannelID = EMPTY,
    token_network_address: typing.TokenNetworkID = EMPTY,
    token: typing.TargetAddress = EMPTY,
    pkey: bytes = EMPTY,
    sender: typing.Address = EMPTY,
) -> LockedTransfer:

    amount = if_empty(amount, UNIT_TRANSFER_AMOUNT)
    initiator = if_empty(initiator, make_address())
    target = if_empty(target, make_address())
    expiration = if_empty(expiration, UNIT_REVEAL_TIMEOUT)
    secret = if_empty(secret, make_secret())
    payment_identifier = if_empty(payment_identifier, 1)
    message_identifier = if_empty(message_identifier, make_message_identifier())
    nonce = if_empty(nonce, 1)
    transferred_amount = if_empty(transferred_amount, 0)
    locked_amount = if_empty(locked_amount, amount)
    locksroot = if_empty(locksroot, EMPTY_MERKLE_ROOT)
    recipient = if_empty(recipient, UNIT_TRANSFER_TARGET)
    channel_identifier = if_empty(channel_identifier, UNIT_CHANNEL_ID)
    token_network_address = if_empty(token_network_address, UNIT_TOKEN_NETWORK_ADDRESS)
    token = if_empty(token, UNIT_TOKEN_ADDRESS)
    pkey = if_empty(pkey, UNIT_TRANSFER_PKEY)
    signer = LocalSigner(pkey)
    sender = if_empty(sender, UNIT_TRANSFER_SENDER)

    assert locked_amount >= amount

    secrethash = sha3(secret)
    lock = Lock(amount=amount, expiration=expiration, secrethash=secrethash)

    if locksroot == EMPTY_MERKLE_ROOT:
        locksroot = sha3(lock.as_bytes)

    transfer = LockedTransfer(
        chain_id=UNIT_CHAIN_ID,
        message_identifier=message_identifier,
        payment_identifier=payment_identifier,
        nonce=nonce,
        token_network_address=token_network_address,
        token=token,
        channel_identifier=channel_identifier,
        transferred_amount=transferred_amount,
        locked_amount=locked_amount,
        recipient=recipient,
        locksroot=locksroot,
        lock=lock,
        target=target,
        initiator=initiator,
    )
    transfer.sign(signer)
    assert transfer.sender == sender
    return transfer


# ALIASES
make_channel = make_channel_state
route_from_channel = make_route_from_channel
make_endstate = make_channel_endstate
make_privkey_address = make_privatekey_address

# CONSTANTS
# In this module constants are in the bottom because we need some of the
# factories.
# Prefixing with UNIT_ to differ from the default globals.
UNIT_SETTLE_TIMEOUT = 50
UNIT_REVEAL_TIMEOUT = 5
UNIT_TRANSFER_AMOUNT = 10
UNIT_TRANSFER_FEE = 5
UNIT_SECRET = b"secretsecretsecretsecretsecretse"
UNIT_SECRETHASH = sha3(UNIT_SECRET)
UNIT_REGISTRY_IDENTIFIER = b"registryregistryregi"
UNIT_TOKEN_ADDRESS = b"tokentokentokentoken"
UNIT_TOKEN_NETWORK_ADDRESS = b"networknetworknetwor"
UNIT_CHANNEL_ID = 1338
UNIT_CHAIN_ID = 337
UNIT_CANONICAL_ID = CanonicalIdentifier(
    chain_identifier=UNIT_CHAIN_ID,
    token_network_address=UNIT_TOKEN_NETWORK_ADDRESS,
    channel_identifier=UNIT_CHANNEL_ID,
)
UNIT_PAYMENT_NETWORK_IDENTIFIER = b"paymentnetworkidentifier"
UNIT_TRANSFER_IDENTIFIER = 37
UNIT_TRANSFER_INITIATOR = b"initiatorinitiatorin"
UNIT_TRANSFER_TARGET = b"targettargettargetta"
UNIT_TRANSFER_PKEY_BIN = sha3(b"transfer pkey")
UNIT_TRANSFER_PKEY = UNIT_TRANSFER_PKEY_BIN
UNIT_TRANSFER_SENDER = privatekey_to_address(sha3(b"transfer pkey"))
HOP1_KEY = b"11111111111111111111111111111111"
HOP2_KEY = b"22222222222222222222222222222222"
HOP3_KEY = b"33333333333333333333333333333333"
HOP4_KEY = b"44444444444444444444444444444444"
HOP5_KEY = b"55555555555555555555555555555555"
HOP6_KEY = b"66666666666666666666666666666666"
HOP1 = privatekey_to_address(HOP1_KEY)
HOP2 = privatekey_to_address(HOP2_KEY)
HOP3 = privatekey_to_address(HOP3_KEY)
HOP4 = privatekey_to_address(HOP4_KEY)
HOP5 = privatekey_to_address(HOP5_KEY)
HOP6 = privatekey_to_address(HOP6_KEY)
ADDR = b"addraddraddraddraddr"
UNIT_TRANSFER_DESCRIPTION = make_transfer_description(secret=UNIT_SECRET)


RANDOM_FACTORIES = {
    typing.Address: make_address,
    typing.Balance: make_balance,
    typing.BlockNumber: make_block_number,
    typing.BlockTimeout: make_block_number,
    typing.ChainID: make_chain_id,
    typing.ChannelID: make_channel_identifier,
    typing.PaymentNetworkID: make_payment_network_identifier,
    typing.TokenNetworkID: make_payment_network_identifier,
    NettingChannelState: make_channel_state,
}


def make_canonical_identifier(
    chain_identifier=UNIT_CHAIN_ID,
    token_network_address=UNIT_TOKEN_NETWORK_ADDRESS,
    channel_identifier=None,
) -> CanonicalIdentifier:
    return CanonicalIdentifier(
        chain_identifier=chain_identifier,
        token_network_address=token_network_address,
        channel_identifier=channel_identifier or make_channel_identifier(),
    )


def make_merkletree_leaves(width: int) -> List[typing.Secret]:
    return [make_secret() for _ in range(width)]


@singledispatch
def create(properties: Any, defaults: Optional[Properties] = None) -> Any:
    """Create objects from their associated property class.

    E. g. a NettingChannelState from NettingChannelStateProperties. For any field in
    properties set to EMPTY a default will be used. The default values can be changed
    by giving another object of the same property type as the defaults argument.
    """
    if isinstance(properties, Properties):
        return properties.TARGET_TYPE(**_properties_to_kwargs(properties, defaults))
    return properties


def _properties_to_kwargs(properties: Properties, defaults: Properties) -> Dict:
    properties = create_properties(properties, defaults or properties.DEFAULTS)
    return {key: create(value) for key, value in properties.__dict__.items()}


@dataclass(frozen=True)
class TransactionExecutionStatusProperties(Properties):
    started_block_number: typing.BlockNumber = EMPTY
    finished_block_number: typing.BlockNumber = EMPTY
    result: str = EMPTY
    TARGET_TYPE = TransactionExecutionStatus


TransactionExecutionStatusProperties.DEFAULTS = TransactionExecutionStatusProperties(
    started_block_number=None,
    finished_block_number=None,
    result=TransactionExecutionStatus.SUCCESS,
)


@dataclass(frozen=True)
class NettingChannelEndStateProperties(Properties):
    address: typing.Address = EMPTY
    privatekey: bytes = EMPTY
    balance: typing.TokenAmount = EMPTY
    merkletree_leaves: typing.MerkleTreeLeaves = EMPTY
    merkletree_width: int = EMPTY
    TARGET_TYPE = NettingChannelEndState


NettingChannelEndStateProperties.DEFAULTS = NettingChannelEndStateProperties(
    address=None, privatekey=None, balance=100, merkletree_leaves=None, merkletree_width=0
)


@create.register(NettingChannelEndStateProperties)  # noqa: F811
def _(properties, defaults=None) -> NettingChannelEndState:
    args = _properties_to_kwargs(properties, defaults or NettingChannelEndStateProperties.DEFAULTS)
    state = NettingChannelEndState(args["address"] or make_address(), args["balance"])

    merkletree_leaves = (
        args["merkletree_leaves"] or make_merkletree_leaves(args["merkletree_width"]) or None
    )
    if merkletree_leaves:
        state.merkletree = MerkleTreeState(compute_layers(merkletree_leaves))

    return state


@dataclass(frozen=True)
class NettingChannelStateProperties(Properties):
    canonical_identifier: CanonicalIdentifier = EMPTY
    token_address: typing.TokenAddress = EMPTY
    payment_network_identifier: typing.PaymentNetworkID = EMPTY

    reveal_timeout: typing.BlockTimeout = EMPTY
    settle_timeout: typing.BlockTimeout = EMPTY
    mediation_fee: typing.FeeAmount = EMPTY

    our_state: NettingChannelEndStateProperties = EMPTY
    partner_state: NettingChannelEndStateProperties = EMPTY

    open_transaction: TransactionExecutionStatusProperties = EMPTY
    close_transaction: TransactionExecutionStatusProperties = EMPTY
    settle_transaction: TransactionExecutionStatusProperties = EMPTY

    TARGET_TYPE = NettingChannelState


NettingChannelStateProperties.DEFAULTS = NettingChannelStateProperties(
    canonical_identifier=make_canonical_identifier(),
    token_address=UNIT_TOKEN_ADDRESS,
    payment_network_identifier=UNIT_PAYMENT_NETWORK_IDENTIFIER,
    reveal_timeout=UNIT_REVEAL_TIMEOUT,
    settle_timeout=UNIT_SETTLE_TIMEOUT,
    mediation_fee=0,
    our_state=NettingChannelEndStateProperties.DEFAULTS,
    partner_state=NettingChannelEndStateProperties.DEFAULTS,
    open_transaction=TransactionExecutionStatusProperties.DEFAULTS,
    close_transaction=None,
    settle_transaction=None,
)


@dataclass(frozen=True)
class BalanceProofProperties(Properties):
    nonce: typing.Nonce = EMPTY
    transferred_amount: typing.TokenAmount = EMPTY
    locked_amount: typing.TokenAmount = EMPTY
    locksroot: typing.Locksroot = EMPTY
    canonical_identifier: CanonicalIdentifier = EMPTY
    TARGET_TYPE = BalanceProofUnsignedState


BalanceProofProperties.DEFAULTS = BalanceProofProperties(
    nonce=1,
    transferred_amount=UNIT_TRANSFER_AMOUNT,
    locked_amount=0,
    locksroot=EMPTY_MERKLE_ROOT,
    canonical_identifier=UNIT_CANONICAL_ID,
)


@dataclass(frozen=True)
class BalanceProofSignedStateProperties(BalanceProofProperties):
    message_hash: typing.AdditionalHash = EMPTY
    signature: typing.Signature = GENERATE
    sender: typing.Address = EMPTY
    pkey: bytes = EMPTY
    TARGET_TYPE = BalanceProofSignedState


BalanceProofSignedStateProperties.DEFAULTS = BalanceProofSignedStateProperties(
    **BalanceProofProperties.DEFAULTS.__dict__,
    sender=UNIT_TRANSFER_SENDER,
    pkey=UNIT_TRANSFER_PKEY,
)


@create.register(BalanceProofSignedStateProperties)  # noqa: F811
def _(properties: BalanceProofSignedStateProperties, defaults=None) -> BalanceProofSignedState:
    defaults = defaults or BalanceProofSignedStateProperties.DEFAULTS
    params = create_properties(properties, defaults).__dict__
    signer = LocalSigner(params.pop("pkey"))

    if params["signature"] is GENERATE:
        keys = ("transferred_amount", "locked_amount", "locksroot")
        balance_hash = hash_balance_data(**_partial_dict(params, *keys))

        data_to_sign = balance_proof.pack_balance_proof(
            balance_hash=balance_hash,
            additional_hash=params["message_hash"],
            canonical_identifier=params["canonical_identifier"],
            nonce=params.get("nonce"),
        )

        params["signature"] = signer.sign(data=data_to_sign)

    return BalanceProofSignedState(**params)


@dataclass(frozen=True)
class LockedTransferProperties(BalanceProofProperties):
    amount: typing.TokenAmount = EMPTY
    expiration: typing.BlockExpiration = EMPTY
    initiator: typing.InitiatorAddress = EMPTY
    target: typing.TargetAddress = EMPTY
    payment_identifier: typing.PaymentID = EMPTY
    token: typing.TokenAddress = EMPTY
    secret: typing.Secret = EMPTY
    TARGET_TYPE = LockedTransferUnsignedState

    @property
    def balance_proof(self):
        return self.extract(BalanceProofProperties)


LockedTransferProperties.DEFAULTS = LockedTransferProperties(
    **create_properties(
        BalanceProofProperties(locked_amount=UNIT_TRANSFER_AMOUNT, transferred_amount=0)
    ).__dict__,
    amount=UNIT_TRANSFER_AMOUNT,
    expiration=UNIT_REVEAL_TIMEOUT,
    initiator=UNIT_TRANSFER_INITIATOR,
    target=UNIT_TRANSFER_TARGET,
    payment_identifier=1,
    token=UNIT_TOKEN_ADDRESS,
    secret=UNIT_SECRET,
)


@create.register(LockedTransferProperties)  # noqa: F811
def _(properties, defaults=None) -> LockedTransferUnsignedState:
    transfer: LockedTransferProperties = create_properties(properties, defaults)
    lock = HashTimeLockState(
        amount=transfer.amount, expiration=transfer.expiration, secrethash=sha3(transfer.secret)
    )
    if transfer.locksroot == EMPTY_MERKLE_ROOT:
        transfer = replace(transfer, locksroot=lock.lockhash)

    return LockedTransferUnsignedState(
        balance_proof=create(transfer.balance_proof),
        lock=lock,
        **transfer.partial_dict("initiator", "target", "payment_identifier", "token"),
    )


@dataclass(frozen=True)
class LockedTransferSignedStateProperties(LockedTransferProperties):
    sender: typing.Address = EMPTY
    recipient: typing.Address = EMPTY
    pkey: bytes = EMPTY
    message_identifier: typing.MessageID = EMPTY
    TARGET_TYPE = LockedTransferSignedState

    @property
    def transfer(self):
        return self.extract(LockedTransferProperties)


LockedTransferSignedStateProperties.DEFAULTS = LockedTransferSignedStateProperties(
    **LockedTransferProperties.DEFAULTS.__dict__,
    sender=UNIT_TRANSFER_SENDER,
    recipient=UNIT_TRANSFER_TARGET,
    pkey=UNIT_TRANSFER_PKEY,
    message_identifier=1,
)


@create.register(LockedTransferSignedStateProperties)  # noqa: F811
def _(properties, defaults=None) -> LockedTransferSignedState:
    transfer: LockedTransferSignedStateProperties = create_properties(properties, defaults)
    params = {key: value for key, value in transfer.__dict__.items()}

    lock = Lock(
        amount=transfer.amount, expiration=transfer.expiration, secrethash=sha3(transfer.secret)
    )

    pkey = params.pop("pkey")
    signer = LocalSigner(pkey)
    sender = params.pop("sender")
    canonical_identifier = params.pop("canonical_identifier")
    params["chain_id"] = int(canonical_identifier.chain_identifier)
    params["channel_identifier"] = int(canonical_identifier.channel_identifier)
    params["token_network_address"] = canonical_identifier.token_network_address
    if params["locksroot"] == EMPTY_MERKLE_ROOT:
        params["locksroot"] = lock.lockhash

    locked_transfer = LockedTransfer(lock=lock, **params)
    locked_transfer.sign(signer)

    assert locked_transfer.sender == sender

    return lockedtransfersigned_from_message(locked_transfer)


SIGNED_TRANSFER_FOR_CHANNEL_DEFAULTS = create_properties(
    LockedTransferSignedStateProperties(expiration=UNIT_SETTLE_TIMEOUT - UNIT_REVEAL_TIMEOUT)
)


def make_signed_transfer_for(
    channel_state: NettingChannelState = EMPTY,
    properties: LockedTransferSignedStateProperties = None,
    defaults: LockedTransferSignedStateProperties = None,
    compute_locksroot: bool = False,
    allow_invalid: bool = False,
    only_transfer: bool = True,
) -> LockedTransferSignedState:
    properties: LockedTransferSignedStateProperties = create_properties(
        properties or LockedTransferSignedStateProperties(),
        defaults or SIGNED_TRANSFER_FOR_CHANNEL_DEFAULTS,
    )

    channel_state = if_empty(channel_state, create(NettingChannelStateProperties()))

    if not allow_invalid:
        expiration = properties.transfer.expiration
        valid = channel_state.reveal_timeout < expiration < channel_state.settle_timeout
        assert valid, "Expiration must be between reveal_timeout and settle_timeout."

    assert privatekey_to_address(properties.pkey) == properties.sender

    if properties.sender == channel_state.our_state.address:
        recipient = channel_state.partner_state.address
    elif properties.sender == channel_state.partner_state.address:
        recipient = channel_state.our_state.address
    else:
        assert False, "Given sender does not participate in given channel."

    if compute_locksroot:
        lock = Lock(
            amount=properties.transfer.amount,
            expiration=properties.transfer.expiration,
            secrethash=sha3(properties.transfer.secret),
        )
        locksroot = merkleroot(
            channel.compute_merkletree_with(
                merkletree=channel_state.partner_state.merkletree, lockhash=sha3(lock.as_bytes)
            )
        )
    else:
        locksroot = properties.transfer.balance_proof.locksroot

    if only_transfer:
        transfer_properties = LockedTransferProperties(
            locksroot=locksroot,
            canonical_identifier=channel_state.canonical_identifier,
            locked_amount=properties.transfer.amount,
        )
    else:
        transfer_properties = LockedTransferProperties(
            locksroot=locksroot, canonical_identifier=channel_state.canonical_identifier
        )
    transfer = create(
        LockedTransferSignedStateProperties(recipient=recipient, **transfer_properties.__dict__),
        defaults=properties,
    )

    if not allow_invalid:
        is_valid, msg, _ = channel.is_valid_lockedtransfer(
            transfer_state=transfer,
            channel_state=channel_state,
            sender_state=channel_state.partner_state,
            receiver_state=channel_state.our_state,
        )
        assert is_valid, msg

    return transfer


def pkeys_from_channel_state(
    properties: NettingChannelStateProperties,
    defaults: NettingChannelStateProperties = NettingChannelStateProperties.DEFAULTS,
) -> Tuple[Optional[bytes], Optional[bytes]]:
    our_key = None
    if properties.our_state is not EMPTY:
        our_key = properties.our_state.privatekey
    elif defaults is not None:
        our_key = defaults.our_state.privatekey

    partner_key = None
    if properties.partner_state is not EMPTY:
        partner_key = properties.partner_state.privatekey
    elif defaults is not None:
        partner_key = defaults.partner_state.privatekey

    return our_key, partner_key


class ChannelSet:
    """Manage a list of channels. The channels can be accessed by subscript."""

    PKEYS = (HOP1_KEY, HOP2_KEY, HOP3_KEY, HOP4_KEY, HOP5_KEY)
    ADDRESSES = (HOP1, HOP2, HOP3, HOP4, HOP5)

    def __init__(
        self,
        channels: List[NettingChannelState],
        our_privatekeys: List[bytes],
        partner_privatekeys: List[bytes],
    ):
        self.channels = channels
        self.our_privatekeys = our_privatekeys
        self.partner_privatekeys = partner_privatekeys

    @property
    def channel_map(self) -> typing.ChannelMap:
        return {channel.identifier: channel for channel in self.channels}

    @property
    def nodeaddresses_to_networkstates(self) -> typing.NodeNetworkStateMap:
        return {channel.partner_state.address: NODE_NETWORK_REACHABLE for channel in self.channels}

    def our_address(self, index: int) -> typing.Address:
        return self.channels[index].our_state.address

    def partner_address(self, index: int) -> typing.Address:
        return self.channels[index].partner_state.address

    def get_route(self, channel_index: int) -> RouteState:
        return route_from_channel(self.channels[channel_index])

    def get_routes(self, *args) -> List[RouteState]:
        return [self.get_route(channel_index) for channel_index in args]

    def __getitem__(self, item: int) -> NettingChannelState:
        return self.channels[item]


def make_channel_set(
    properties: List[NettingChannelStateProperties] = None,
    defaults: NettingChannelStateProperties = NettingChannelStateProperties.DEFAULTS,
    number_of_channels: int = None,
) -> ChannelSet:
    if number_of_channels is None:
        number_of_channels = len(properties)

    channels = list()
    our_pkeys = [None] * number_of_channels
    partner_pkeys = [None] * number_of_channels

    if properties is None:
        properties = list()
    while len(properties) < number_of_channels:
        properties.append(NettingChannelStateProperties())

    for i in range(number_of_channels):
        our_pkeys[i], partner_pkeys[i] = pkeys_from_channel_state(properties[i], defaults)
        channels.append(create(properties[i], defaults))

    return ChannelSet(channels, our_pkeys, partner_pkeys)


def mediator_make_channel_pair(
    defaults: NettingChannelStateProperties = None,
    amount: typing.TokenAmount = UNIT_TRANSFER_AMOUNT,
) -> ChannelSet:
    properties_list = [
        NettingChannelStateProperties(
            canonical_identifier=make_canonical_identifier(channel_identifier=1),
            partner_state=NettingChannelEndStateProperties(
                address=UNIT_TRANSFER_SENDER, balance=amount
            ),
        ),
        NettingChannelStateProperties(
            canonical_identifier=make_canonical_identifier(channel_identifier=2),
            our_state=NettingChannelEndStateProperties(balance=amount),
            partner_state=NettingChannelEndStateProperties(address=UNIT_TRANSFER_TARGET),
        ),
    ]

    return make_channel_set(properties_list, defaults)


def mediator_make_init_action(
    channels: ChannelSet, transfer: LockedTransferSignedState
) -> ActionInitMediator:
    return ActionInitMediator(channels.get_routes(1), channels.get_route(0), transfer)


class MediatorTransfersPair(NamedTuple):
    channels: ChannelSet
    transfers_pair: List[MediationPairState]
    amount: int
    block_number: typing.BlockNumber
    block_hash: typing.BlockHash

    @property
    def channel_map(self) -> typing.ChannelMap:
        return self.channels.channel_map


def make_transfers_pair(
    number_of_channels: int, amount: int = UNIT_TRANSFER_AMOUNT, block_number: int = 5
) -> MediatorTransfersPair:

    deposit = 5 * amount
    defaults = create_properties(
        NettingChannelStateProperties(
            our_state=NettingChannelEndStateProperties(balance=deposit),
            partner_state=NettingChannelEndStateProperties(balance=deposit),
            open_transaction=TransactionExecutionStatusProperties(finished_block_number=10),
        )
    )
    properties_list = [
        NettingChannelStateProperties(
            canonical_identifier=make_canonical_identifier(channel_identifier=i),
            our_state=NettingChannelEndStateProperties(
                address=ChannelSet.ADDRESSES[0], privatekey=ChannelSet.PKEYS[0]
            ),
            partner_state=NettingChannelEndStateProperties(
                address=ChannelSet.ADDRESSES[i + 1], privatekey=ChannelSet.PKEYS[i + 1]
            ),
        )
        for i in range(number_of_channels)
    ]
    channels = make_channel_set(properties_list, defaults)

    lock_expiration = block_number + UNIT_REVEAL_TIMEOUT * 2
    pseudo_random_generator = random.Random()
    transfers_pairs = list()

    for payer_index in range(number_of_channels - 1):
        payee_index = payer_index + 1

        receiver_channel = channels[payer_index]
        received_transfer = create(
            LockedTransferSignedStateProperties(
                amount=amount,
                expiration=lock_expiration,
                payment_identifier=UNIT_TRANSFER_IDENTIFIER,
                canonical_identifier=receiver_channel.canonical_identifier,
                sender=channels.partner_address(payer_index),
                pkey=channels.partner_privatekeys[payer_index],
            )
        )

        is_valid, _, msg = channel.handle_receive_lockedtransfer(
            receiver_channel, received_transfer
        )
        assert is_valid, msg

        message_identifier = message_identifier_from_prng(pseudo_random_generator)
        lockedtransfer_event = channel.send_lockedtransfer(
            channel_state=channels[payee_index],
            initiator=UNIT_TRANSFER_INITIATOR,
            target=UNIT_TRANSFER_TARGET,
            amount=amount,
            message_identifier=message_identifier,
            payment_identifier=UNIT_TRANSFER_IDENTIFIER,
            expiration=lock_expiration,
            secrethash=UNIT_SECRETHASH,
        )
        assert lockedtransfer_event

        lock_timeout = lock_expiration - block_number
        assert mediator.is_channel_usable(
            candidate_channel_state=channels[payee_index],
            transfer_amount=amount,
            lock_timeout=lock_timeout,
        )
        sent_transfer = lockedtransfer_event.transfer

        pair = MediationPairState(received_transfer, lockedtransfer_event.recipient, sent_transfer)
        transfers_pairs.append(pair)

    return MediatorTransfersPair(
        channels=channels,
        transfers_pair=transfers_pairs,
        amount=amount,
        block_number=block_number,
        block_hash=make_block_hash(),
    )


def make_node_availability_map(nodes):
    return {node: NODE_NETWORK_REACHABLE for node in nodes}


@dataclass(frozen=True)
class RouteProperties(Properties):
    address1: typing.Address
    address2: typing.Address
    capacity1to2: typing.TokenAmount
    capacity2to1: typing.TokenAmount = 0


def route_properties_to_channel(route: RouteProperties) -> NettingChannelState:
    channel = create(
        NettingChannelStateProperties(
            canonical_identifier=make_canonical_identifier(),
            our_state=NettingChannelEndStateProperties(
                address=route.address1, balance=route.capacity1to2
            ),
            partner_state=NettingChannelEndStateProperties(
                address=route.address2, balance=route.capacity2to1
            ),
        )
    )
    return channel  # type: ignore


def create_network(
    token_network_state: TokenNetworkState,
    our_address: typing.Address,
    routes: List[RouteProperties],
    block_number: typing.BlockNumber,
    block_hash: typing.BlockHash = None,
) -> Tuple[Any, List[NettingChannelState]]:
    """Creates a network from route properties.

    If the address in the route is our_address, create a channel also.
    Returns a list of created channels and the new state.
    """

    block_hash = block_hash or make_block_hash()
    state = token_network_state
    channels = list()

    for count, route in enumerate(routes, 1):
        if route.address1 == our_address:
            channel = route_properties_to_channel(route)
            state_change = ContractReceiveChannelNew(
                transaction_hash=make_transaction_hash(),
                channel_state=channel,
                block_number=block_number,
                block_hash=block_hash,
            )
            channels.append(channel)
        else:
            state_change = ContractReceiveRouteNew(
                transaction_hash=make_transaction_hash(),
                canonical_identifier=make_canonical_identifier(),
                participant1=route.address1,
                participant2=route.address2,
                block_number=block_number,
                block_hash=block_hash,
            )

        iteration = token_network.state_transition(
            token_network_state=state,
            state_change=state_change,
            block_number=block_number,
            block_hash=block_hash,
        )
        state = iteration.new_state

        assert len(state.network_graph.channel_identifier_to_participants) == count
        assert len(state.network_graph.network.edges()) == count

    return state, channels
