import json
import importlib

import networkx
from eth_utils import (
    to_checksum_address,
    to_canonical_address,
    to_bytes,
    to_hex,
)

from raiden.utils import typing
from raiden.transfer.merkle_tree import (
    LEAVES,
    compute_layers,
)


class RaidenJSONEncoder(json.JSONEncoder):
    """ A custom JSON encoder to provide convenience
    of recursive instance encoding. """
    def default(self, obj):
        """
        If an object has `to_dict` method,
        call that method.
        """
        if hasattr(obj, 'to_dict'):
            result = obj.to_dict()
            result['_type'] = f'{obj.__module__}.{obj.__class__.__name__}'
            return result
        return super().default(obj)


class RaidenJSONDecoder(json.JSONDecoder):
    """ A custom JSON decoder which facilitates
    specific object type invocation to restore
    it's state.
    """
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, data):
        """
        detects the type of a JSON object, imports the class
        of that type and calls `to_dict`
        """
        if '_type' in data:
            _type = data['_type']
            klass = self._import_type(_type)
            if hasattr(klass, 'from_dict'):
                return klass.from_dict(data)
        return json.JSONDecoder.object_hook(data)

    def _import_type(self, type_name):
        *module_name, klass_name = type_name.split('.')
        module_name = '.'.join(module_name)
        module = importlib.import_module(module_name, None)
        if not hasattr(module, klass_name):
            raise TypeError(f'Could not find {module_name}.{klass_name}')
        klass = getattr(module, klass_name)
        return klass


def json_encode(obj):
    return json.dumps(obj, cls=RaidenJSONEncoder)


def json_decode(obj):
    return json.loads(obj, cls=RaidenJSONDecoder)


def map_dict(
    key_func: typing.Callable,
    value_func: typing.Callable,
    dict: typing.Dict,
) -> typing.Dict[str, typing.Any]:
    return {
        key_func(k): value_func(v)
        for k, v in dict.items()
    }


def map_list(
    value_func: typing.Callable,
    list: typing.List,
) -> typing.List[typing.Any]:
    return [
        value_func(v)
        for v in list
    ]


def serialize_bytes(data: bytes) -> str:
    return to_hex(data)


def deserialize_bytes(data: str) -> bytes:
    return to_bytes(hexstr=data)


def serialize_networkx_graph(graph: networkx.Graph) -> str:
    return json.dumps([
        edge for edge in graph.edges
    ])


def deserialize_networkx_graph(data: str) -> networkx.Graph:
    return networkx.Graph(json.loads(data))


def serialize_participants_tuple(
    participants: typing.Tuple[typing.Address, typing.Address],
) -> typing.List[str]:
    return [
        to_checksum_address(participants[0]),
        to_checksum_address(participants[1]),
    ]


def deserialize_participants_tuple(
    data: typing.List[str],
) -> typing.Tuple[typing.Address, typing.Address]:
    assert len(data) == 2
    return (
        to_canonical_address(data[0]),
        to_canonical_address(data[1]),
    )


def serialize_merkletree_layers(data) -> typing.List[str]:
    return map_list(serialize_bytes, data[LEAVES])


def deserialize_merkletree_layers(data: typing.List[str]):
    elements = map_list(deserialize_bytes, data)
    return compute_layers(elements)