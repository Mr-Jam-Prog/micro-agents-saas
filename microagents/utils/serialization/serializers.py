"""
Serialization utilities for MicroAgents Platform.
Multiple formats with compression, encryption, and validation.
"""

import asyncio
import base64
import csv
import functools
import gzip
import io
import json
import pickle
import sys
import tempfile
import time
import uuid
import warnings
import zlib
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable, Generator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from functools import lru_cache, partial
from io import BytesIO, StringIO
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    TextIO,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import brotli
import msgpack
import orjson
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import rapidjson
import simplejson
import yaml
from cryptography.fernet import Fernet, InvalidToken
from fastavro import parse_schema, reader, writer
from google.protobuf import descriptor_pool, message_factory
from google.protobuf.json_format import MessageToDict, MessageToJson, ParseDict
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, validator
from pydantic.json import pydantic_encoder

# Type variables
T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)
P = TypeVar("P", bound="ProtobufMessage")

# Custom exceptions
class SerializationError(Exception):
    """Base exception for serialization errors."""
    pass

class DeserializationError(Exception):
    """Base exception for deserialization errors."""
    pass

class SchemaValidationError(SerializationError):
    """Schema validation failed."""
    pass

class VersionCompatibilityError(SerializationError):
    """Version compatibility error."""
    pass

class CompressionError(SerializationError):
    """Compression/decompression error."""
    pass

class EncryptionError(SerializationError):
    """Encryption/decryption error."""
    pass


# Custom encoders for complex types
class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for complex Python types."""
    
    def default(self, obj: Any) -> Any:
        # Handle Pydantic models
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        
        # Handle datetime objects
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        
        # Handle Decimal
        if isinstance(obj, Decimal):
            return float(obj)
        
        # Handle UUID
        if isinstance(obj, uuid.UUID):
            return str(obj)
        
        # Handle Enum
        if isinstance(obj, Enum):
            return obj.value
        
        # Handle bytes
        if isinstance(obj, bytes):
            return base64.b64encode(obj).decode('utf-8')
        
        # Handle sets
        if isinstance(obj, set):
            return list(obj)
        
        # Handle generators/iterators
        if hasattr(obj, '__next__') or hasattr(obj, '__iter__'):
            return list(obj)
        
        # Handle complex numbers
        if isinstance(obj, complex):
            return {"real": obj.real, "imag": obj.imag}
        
        # Handle numpy arrays if available
        try:
            import numpy as np
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, np.generic):
                return obj.item()
        except ImportError:
            pass
        
        # Handle pandas objects if available
        try:
            import pandas as pd
            if isinstance(obj, pd.DataFrame):
                return obj.to_dict(orient='records')
            if isinstance(obj, pd.Series):
                return obj.to_dict()
        except ImportError:
            pass
        
        # Handle PyArrow tables if available
        try:
            import pyarrow as pa
            if isinstance(obj, pa.Table):
                return obj.to_pydict()
        except ImportError:
            pass
        
        # Handle circular references via object id tracking
        if hasattr(self, '_seen'):
            if id(obj) in self._seen:
                return {"$ref": str(id(obj)), "$type": obj.__class__.__name__}
            self._seen.add(id(obj))
        
        # Try to use Pydantic's encoder as fallback
        try:
            return pydantic_encoder(obj)
        except TypeError:
            # Last resort: use repr
            return repr(obj)
    
    def encode(self, obj: Any) -> str:
        """Encode with circular reference tracking."""
        self._seen = set()
        try:
            return super().encode(obj)
        finally:
            delattr(self, '_seen')


class CircularReferenceHandler:
    """Handler for circular references during serialization."""
    
    def __init__(self):
        self.seen: Dict[int, str] = {}
        self.ref_counter = 0
    
    def reset(self) -> None:
        """Reset the handler state."""
        self.seen.clear()
        self.ref_counter = 0
    
    def track(self, obj: Any) -> Optional[str]:
        """
        Track object and return reference if already seen.
        
        Args:
            obj: Object to track
            
        Returns:
            Reference ID if already seen, None otherwise
        """
        obj_id = id(obj)
        
        if obj_id in self.seen:
            return self.seen[obj_id]
        
        ref_id = f"ref_{self.ref_counter}"
        self.seen[obj_id] = ref_id
        self.ref_counter += 1
        return None
    
    def resolve(self, data: Any) -> Any:
        """
        Resolve circular references in deserialized data.
        
        Args:
            data: Data with references
            
        Returns:
            Data with references resolved
        """
        if isinstance(data, dict):
            # Check for reference
            if "$ref" in data and "$id" in data:
                ref_id = data["$id"]
                # Store reference for later resolution
                return {"$ref": ref_id}
            
            # Recursively process dict
            return {k: self.resolve(v) for k, v in data.items()}
        
        elif isinstance(data, list):
            return [self.resolve(item) for item in data]
        
        return data


class SchemaRegistry:
    """Registry for data schemas with versioning."""
    
    def __init__(self):
        self.schemas: Dict[str, Dict[int, Any]] = defaultdict(dict)
        self.migration_paths: Dict[Tuple[str, int, int], Callable] = {}
        
    def register_schema(
        self,
        name: str,
        version: int,
        schema: Any,
        migrations: Optional[Dict[int, Callable]] = None,
    ) -> None:
        """
        Register a schema with version.
        
        Args:
            name: Schema name
            version: Schema version
            schema: Schema definition
            migrations: Migration functions from older versions
        """
        self.schemas[name][version] = schema
        
        if migrations:
            for from_version, migration_func in migrations.items():
                self.migration_paths[(name, from_version, version)] = migration_func
    
    def get_schema(self, name: str, version: int) -> Optional[Any]:
        """
        Get schema by name and version.
        
        Args:
            name: Schema name
            version: Schema version
            
        Returns:
            Schema or None if not found
        """
        return self.schemas[name].get(version)
    
    def get_latest_version(self, name: str) -> Optional[Tuple[int, Any]]:
        """
        Get latest version of a schema.
        
        Args:
            name: Schema name
            
        Returns:
            Tuple of (version, schema) or None
        """
        if name not in self.schemas or not self.schemas[name]:
            return None
        
        latest_version = max(self.schemas[name].keys())
        return latest_version, self.schemas[name][latest_version]
    
    def migrate_data(
        self,
        name: str,
        data: Any,
        from_version: int,
        to_version: int,
    ) -> Any:
        """
        Migrate data between schema versions.
        
        Args:
            name: Schema name
            data: Data to migrate
            from_version: Current data version
            to_version: Target version
            
        Returns:
            Migrated data
            
        Raises:
            VersionCompatibilityError: If migration path doesn't exist
        """
        if from_version == to_version:
            return data
        
        # Check direct migration
        key = (name, from_version, to_version)
        if key in self.migration_paths:
            return self.migration_paths[key](data)
        
        # Try to find path through intermediate versions
        # This is a simplified version - in production you'd want a proper graph search
        all_versions = sorted(self.schemas[name].keys())
        
        if from_version not in all_versions or to_version not in all_versions:
            raise VersionCompatibilityError(
                f"Cannot migrate {name} from {from_version} to {to_version}"
            )
        
        # Simple linear migration through intermediate versions
        current_data = data
        current_version = from_version
        
        while current_version != to_version:
            # Find next version with migration
            next_version = None
            for v in all_versions:
                if v > current_version:
                    key = (name, current_version, v)
                    if key in self.migration_paths:
                        next_version = v
                        break
            
            if next_version is None:
                raise VersionCompatibilityError(
                    f"No migration path from {current_version} to {to_version}"
                )
            
            current_data = self.migration_paths[(name, current_version, next_version)](current_data)
            current_version = next_version
        
        return current_data


class Serializer(ABC):
    """Abstract base class for serializers."""
    
    def __init__(self, circular_ref_handler: Optional[CircularReferenceHandler] = None):
        self.circular_ref_handler = circular_ref_handler or CircularReferenceHandler()
        self.schema_registry = SchemaRegistry()
        
    @abstractmethod
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to bytes."""
        pass
    
    @abstractmethod
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize bytes to data."""
        pass
    
    def serialize_to_file(self, data: Any, filepath: Union[str, Path], **kwargs) -> None:
        """Serialize data to file."""
        serialized = self.serialize(data, **kwargs)
        with open(filepath, 'wb') as f:
            f.write(serialized)
    
    def deserialize_from_file(self, filepath: Union[str, Path], **kwargs) -> Any:
        """Deserialize data from file."""
        with open(filepath, 'rb') as f:
            data = f.read()
        return self.deserialize(data, **kwargs)
    
    @contextmanager
    def serialize_stream(self, output: BinaryIO, **kwargs) -> Generator:
        """Context manager for streaming serialization."""
        yield SerializationStream(self, output, **kwargs)
    
    @contextmanager
    def deserialize_stream(self, input: BinaryIO, **kwargs) -> Generator:
        """Context manager for streaming deserialization."""
        yield DeserializationStream(self, input, **kwargs)


class SerializationStream:
    """Stream for incremental serialization."""
    
    def __init__(self, serializer: Serializer, output: BinaryIO, **kwargs):
        self.serializer = serializer
        self.output = output
        self.kwargs = kwargs
        
    def write(self, data: Any) -> None:
        """Write serialized data to stream."""
        serialized = self.serializer.serialize(data, **self.kwargs)
        self.output.write(serialized)


class DeserializationStream:
    """Stream for incremental deserialization."""
    
    def __init__(self, serializer: Serializer, input: BinaryIO, **kwargs):
        self.serializer = serializer
        self.input = input
        self.kwargs = kwargs
        
    def read(self, size: Optional[int] = None) -> Any:
        """Read and deserialize data from stream."""
        data = self.input.read(size) if size else self.input.read()
        return self.serializer.deserialize(data, **self.kwargs)


class JSONSerializer(Serializer):
    """JSON serialization with multiple backends."""
    
    def __init__(
        self,
        backend: str = "orjson",  # "json", "simplejson", "rapidjson", "orjson"
        indent: Optional[int] = None,
        sort_keys: bool = False,
        ensure_ascii: bool = False,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.backend = backend
        self.indent = indent
        self.sort_keys = sort_keys
        self.ensure_ascii = ensure_ascii
        
        # Configure encoder based on backend
        if backend == "json":
            self.encoder = CustomJSONEncoder
        elif backend == "simplejson":
            self.encoder = simplejson.JSONEncoder
        elif backend == "rapidjson":
            self.encoder = rapidjson.Encoder
        elif backend == "orjson":
            # orjson doesn't use an encoder class
            pass
        else:
            raise ValueError(f"Unsupported JSON backend: {backend}")
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to JSON bytes."""
        # Handle circular references
        self.circular_ref_handler.reset()
        data = self._handle_circular_refs(data)
        
        # Use specified backend
        if self.backend == "orjson":
            options = 0
            if not self.ensure_ascii:
                options |= orjson.OPT_NON_STR_KEYS
            if self.indent:
                options |= orjson.OPT_INDENT_2
            
            try:
                return orjson.dumps(data, option=options)
            except (TypeError, ValueError) as e:
                # Fallback to custom encoder for complex types
                json_str = json.dumps(data, cls=CustomJSONEncoder, indent=self.indent)
                return json_str.encode('utf-8')
        
        elif self.backend == "rapidjson":
            # rapidjson doesn't handle complex types well
            json_str = rapidjson.dumps(data, ensure_ascii=self.ensure_ascii, indent=self.indent)
            return json_str.encode('utf-8')
        
        elif self.backend == "simplejson":
            json_str = simplejson.dumps(
                data,
                indent=self.indent,
                sort_keys=self.sort_keys,
                ensure_ascii=self.ensure_ascii,
                ignore_nan=True,
            )
            return json_str.encode('utf-8')
        
        else:  # standard json
            json_str = json.dumps(
                data,
                cls=CustomJSONEncoder,
                indent=self.indent,
                sort_keys=self.sort_keys,
                ensure_ascii=self.ensure_ascii,
            )
            return json_str.encode('utf-8')
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize JSON bytes to data."""
        # Parse JSON
        if self.backend == "orjson":
            result = orjson.loads(data)
        elif self.backend == "rapidjson":
            result = rapidjson.loads(data.decode('utf-8'))
        elif self.backend == "simplejson":
            result = simplejson.loads(data.decode('utf-8'))
        else:
            result = json.loads(data.decode('utf-8'))
        
        # Resolve circular references
        return self.circular_ref_handler.resolve(result)
    
    def _handle_circular_refs(self, data: Any, parent_ref: Optional[str] = None) -> Any:
        """
        Handle circular references during serialization.
        
        Args:
            data: Data to process
            parent_ref: Parent reference ID
            
        Returns:
            Data with circular references handled
        """
        if isinstance(data, (str, int, float, bool, type(None))):
            return data
        
        if isinstance(data, bytes):
            return base64.b64encode(data).decode('utf-8')
        
        # Check for circular reference
        ref_id = self.circular_ref_handler.track(data)
        if ref_id:
            return {"$ref": ref_id, "$id": parent_ref or "root"}
        
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                # Convert non-string keys
                if not isinstance(key, str):
                    key = str(key)
                result[key] = self._handle_circular_refs(value, ref_id)
            return result
        
        elif isinstance(data, (list, tuple, set)):
            return [self._handle_circular_refs(item, ref_id) for item in data]
        
        # Handle other types via CustomJSONEncoder
        encoder = CustomJSONEncoder()
        return encoder.default(data)


class YAMLSerializer(Serializer):
    """YAML serialization with safe loading."""
    
    def __init__(
        self,
        safe_load: bool = True,
        default_flow_style: bool = False,
        explicit_start: bool = False,
        explicit_end: bool = False,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.safe_load = safe_load
        self.default_flow_style = default_flow_style
        self.explicit_start = explicit_start
        self.explicit_end = explicit_end
        
        # Configure YAML dumper
        self.dumper = yaml.SafeDumper if safe_load else yaml.Dumper
        
        # Add custom representers
        self._add_custom_representers()
    
    def _add_custom_representers(self) -> None:
        """Add custom YAML representers for complex types."""
        
        def datetime_representer(dumper: Any, data: datetime) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:timestamp', data.isoformat())
        
        def date_representer(dumper: Any, data: date) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:str', data.isoformat())
        
        def decimal_representer(dumper: Any, data: Decimal) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:float', float(data))
        
        def uuid_representer(dumper: Any, data: uuid.UUID) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:str', str(data))
        
        def enum_representer(dumper: Any, data: Enum) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:str', str(data.value))
        
        def bytes_representer(dumper: Any, data: bytes) -> Any:
            return dumper.represent_scalar('tag:yaml.org,2002:binary', base64.b64encode(data).decode('utf-8'))
        
        # Register representers
        self.dumper.add_representer(datetime, datetime_representer)
        self.dumper.add_representer(date, date_representer)
        self.dumper.add_representer(Decimal, decimal_representer)
        self.dumper.add_representer(uuid.UUID, uuid_representer)
        self.dumper.add_representer(Enum, enum_representer)
        self.dumper.add_representer(bytes, bytes_representer)
        
        # Handle BaseModel
        def pydantic_representer(dumper: Any, data: BaseModel) -> Any:
            return dumper.represent_dict(data.model_dump())
        
        self.dumper.add_multi_representer(BaseModel, pydantic_representer)
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to YAML bytes."""
        # Handle circular references
        self.circular_ref_handler.reset()
        data = self._handle_circular_refs(data)
        
        yaml_str = yaml.dump(
            data,
            Dumper=self.dumper,
            default_flow_style=self.default_flow_style,
            explicit_start=self.explicit_start,
            explicit_end=self.explicit_end,
            allow_unicode=True,
            encoding='utf-8',
            **kwargs,
        )
        
        if isinstance(yaml_str, str):
            return yaml_str.encode('utf-8')
        return yaml_str
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize YAML bytes to data."""
        loader = yaml.SafeLoader if self.safe_load else yaml.Loader
        
        # Add custom constructors
        self._add_custom_constructors(loader)
        
        try:
            result = yaml.load(data.decode('utf-8'), Loader=loader)
        except yaml.YAMLError as e:
            raise DeserializationError(f"YAML parsing error: {e}")
        
        # Resolve circular references
        return self.circular_ref_handler.resolve(result)
    
    def _add_custom_constructors(self, loader: Any) -> None:
        """Add custom YAML constructors."""
        
        def datetime_constructor(loader: Any, node: Any) -> datetime:
            value = loader.construct_scalar(node)
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        
        def date_constructor(loader: Any, node: Any) -> date:
            value = loader.construct_scalar(node)
            return date.fromisoformat(value)
        
        def binary_constructor(loader: Any, node: Any) -> bytes:
            value = loader.construct_scalar(node)
            return base64.b64decode(value)
        
        # Register constructors
        loader.add_constructor('tag:yaml.org,2002:timestamp', datetime_constructor)
        loader.add_constructor('tag:yaml.org,2002:python/object/apply:datetime.datetime', datetime_constructor)
        loader.add_constructor('tag:yaml.org,2002:date', date_constructor)
        loader.add_constructor('tag:yaml.org,2002:binary', binary_constructor)
    
    def _handle_circular_refs(self, data: Any, parent_ref: Optional[str] = None) -> Any:
        """Handle circular references for YAML."""
        if isinstance(data, (str, int, float, bool, type(None))):
            return data
        
        # Check for circular reference
        ref_id = self.circular_ref_handler.track(data)
        if ref_id:
            return {"$ref": ref_id, "$id": parent_ref or "root"}
        
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                result[key] = self._handle_circular_refs(value, ref_id)
            return result
        
        elif isinstance(data, (list, tuple, set)):
            return [self._handle_circular_refs(item, ref_id) for item in data]
        
        # Handle other types via YAML representers
        return data


class MessagePackSerializer(Serializer):
    """MessagePack serialization for high performance."""
    
    def __init__(
        self,
        use_bin_type: bool = True,
        raw: bool = False,
        strict_types: bool = False,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.use_bin_type = use_bin_type
        self.raw = raw
        self.strict_types = strict_types
        
        # Custom packer/unpacker for complex types
        self._default = self._create_default_handler()
    
    def _create_default_handler(self) -> Callable:
        """Create default handler for complex types."""
        
        def default(obj: Any) -> Any:
            # Handle datetime
            if isinstance(obj, datetime):
                return {
                    "__type__": "datetime",
                    "value": obj.isoformat(),
                }
            
            # Handle date
            if isinstance(obj, date):
                return {
                    "__type__": "date",
                    "value": obj.isoformat(),
                }
            
            # Handle Decimal
            if isinstance(obj, Decimal):
                return {
                    "__type__": "decimal",
                    "value": str(obj),
                }
            
            # Handle UUID
            if isinstance(obj, uuid.UUID):
                return {
                    "__type__": "uuid",
                    "value": str(obj),
                }
            
            # Handle Enum
            if isinstance(obj, Enum):
                return {
                    "__type__": "enum",
                    "value": obj.value,
                }
            
            # Handle BaseModel
            if isinstance(obj, BaseModel):
                return {
                    "__type__": "pydantic",
                    "value": obj.model_dump(mode="json"),
                }
            
            # Handle bytes (already handled by use_bin_type)
            if isinstance(obj, bytes):
                return obj
            
            # Handle circular reference
            ref_id = self.circular_ref_handler.track(obj)
            if ref_id:
                return {
                    "__type__": "ref",
                    "ref_id": ref_id,
                }
            
            # Last resort: convert to string
            return str(obj)
        
        return default
    
    def _object_hook(self, obj: Dict[str, Any]) -> Any:
        """Object hook for deserialization."""
        if "__type__" in obj:
            type_name = obj["__type__"]
            value = obj["value"]
            
            if type_name == "datetime":
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            elif type_name == "date":
                return date.fromisoformat(value)
            elif type_name == "decimal":
                return Decimal(value)
            elif type_name == "uuid":
                return uuid.UUID(value)
            elif type_name == "enum":
                # Need to know the enum type - this is a limitation
                return value
            elif type_name == "pydantic":
                # Need to know the model type - this is a limitation
                return value
            elif type_name == "ref":
                # Store reference for later resolution
                return {"$ref": value}
        
        return obj
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to MessagePack bytes."""
        self.circular_ref_handler.reset()
        
        try:
            return msgpack.packb(
                data,
                default=self._default,
                use_bin_type=self.use_bin_type,
                strict_types=self.strict_types,
            )
        except (TypeError, ValueError) as e:
            raise SerializationError(f"MessagePack serialization failed: {e}")
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize MessagePack bytes to data."""
        try:
            result = msgpack.unpackb(
                data,
                object_hook=self._object_hook,
                raw=self.raw,
                strict_map_key=False,
            )
        except (msgpack.UnpackException, ValueError) as e:
            raise DeserializationError(f"MessagePack deserialization failed: {e}")
        
        # Resolve circular references
        return self.circular_ref_handler.resolve(result)


class AvroSerializer(Serializer):
    """Avro serialization with schema support."""
    
    def __init__(
        self,
        schema: Optional[Dict[str, Any]] = None,
        schema_registry_url: Optional[str] = None,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.schema = schema
        self.schema_registry_url = schema_registry_url
        
        if schema:
            self.parsed_schema = parse_schema(schema)
        else:
            self.parsed_schema = None
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to Avro bytes."""
        if not self.parsed_schema:
            raise SerializationError("Avro schema is required for serialization")
        
        # Convert data to match schema
        data = self._convert_to_avro(data)
        
        # Write to bytes buffer
        buffer = BytesIO()
        try:
            writer(buffer, self.parsed_schema, [data])
            return buffer.getvalue()
        except Exception as e:
            raise SerializationError(f"Avro serialization failed: {e}")
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize Avro bytes to data."""
        if not self.parsed_schema:
            raise DeserializationError("Avro schema is required for deserialization")
        
        buffer = BytesIO(data)
        try:
            records = list(reader(buffer))
            if not records:
                return None
            return self._convert_from_avro(records[0])
        except Exception as e:
            raise DeserializationError(f"Avro deserialization failed: {e}")
    
    def _convert_to_avro(self, data: Any) -> Any:
        """Convert Python data to Avro-compatible format."""
        if isinstance(data, (str, int, float, bool, type(None), bytes)):
            return data
        
        if isinstance(data, datetime):
            # Convert to microseconds since epoch
            return int(data.timestamp() * 1_000_000)
        
        if isinstance(data, date):
            # Convert to days since epoch (1970-01-01)
            return (data - date(1970, 1, 1)).days
        
        if isinstance(data, dict):
            return {str(k): self._convert_to_avro(v) for k, v in data.items()}
        
        if isinstance(data, (list, tuple)):
            return [self._convert_to_avro(item) for item in data]
        
        if isinstance(data, Enum):
            return data.value
        
        if isinstance(data, uuid.UUID):
            return str(data)
        
        if isinstance(data, Decimal):
            return str(data)
        
        # Try to convert to dict
        if hasattr(data, '__dict__'):
            return self._convert_to_avro(data.__dict__)
        
        return str(data)
    
    def _convert_from_avro(self, data: Any) -> Any:
        """Convert Avro data to Python format."""
        if isinstance(data, dict):
            # Check for special types
            if "__datetime__" in data:
                return datetime.fromtimestamp(data["value"] / 1_000_000)
            if "__date__" in data:
                return date(1970, 1, 1) + timedelta(days=data["value"])
            
            return {k: self._convert_from_avro(v) for k, v in data.items()}
        
        if isinstance(data, list):
            return [self._convert_from_avro(item) for item in data]
        
        return data


class ProtobufSerializer(Serializer):
    """Protocol Buffers serialization."""
    
    def __init__(
        self,
        message_type: Optional[Type] = None,
        descriptor_file: Optional[str] = None,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.message_type = message_type
        
        if descriptor_file:
            self._load_from_descriptor(descriptor_file)
        elif message_type:
            self.message_type = message_type
        else:
            self.message_type = None
    
    def _load_from_descriptor(self, descriptor_file: str) -> None:
        """Load message type from descriptor file."""
        try:
            from google.protobuf import descriptor_pb2
            from google.protobuf import message_factory
            
            # Load descriptor
            with open(descriptor_file, 'rb') as f:
                descriptor_data = f.read()
            
            # Create message class
            file_descriptor = descriptor_pb2.FileDescriptorProto()
            file_descriptor.ParseFromString(descriptor_data)
            
            descriptor_pool_instance = descriptor_pool.Default()
            descriptor_pool_instance.Add(file_descriptor)
            
            message_factory_instance = message_factory.MessageFactory(descriptor_pool_instance)
            self.message_type = message_factory_instance.GetMessageType(file_descriptor.message_type[0].name)
            
        except Exception as e:
            raise SerializationError(f"Failed to load protobuf descriptor: {e}")
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to Protobuf bytes."""
        if not self.message_type:
            raise SerializationError("Protobuf message type is required for serialization")
        
        try:
            # Convert data to protobuf message
            if isinstance(data, dict):
                message = ParseDict(data, self.message_type())
            elif isinstance(data, self.message_type):
                message = data
            else:
                # Try to convert
                message = self.message_type()
                message.ParseFromString(str(data).encode())
            
            return message.SerializeToString()
            
        except Exception as e:
            raise SerializationError(f"Protobuf serialization failed: {e}")
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize Protobuf bytes to data."""
        if not self.message_type:
            raise DeserializationError("Protobuf message type is required for deserialization")
        
        try:
            message = self.message_type()
            message.ParseFromString(data)
            
            # Convert to dict for easier use
            return MessageToDict(
                message,
                preserving_proto_field_name=True,
                including_default_value_fields=True,
            )
            
        except Exception as e:
            raise DeserializationError(f"Protobuf deserialization failed: {e}")


class CSVSerializer(Serializer):
    """CSV serialization with advanced features."""
    
    def __init__(
        self,
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.delimiter = delimiter
        self.quotechar = quotechar
        self.quoting = quoting
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to CSV bytes."""
        # Handle different data types
        if isinstance(data, dict):
            data = [data]
        
        if not isinstance(data, list):
            raise SerializationError("CSV serialization requires list of dicts")
        
        if not data:
            return b""
        
        # Get fieldnames
        fieldnames = kwargs.get('fieldnames')
        if not fieldnames:
            fieldnames = set()
            for row in data:
                fieldnames.update(row.keys())
            fieldnames = list(fieldnames)
        
        # Write CSV
        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=fieldnames,
            delimiter=self.delimiter,
            quotechar=self.quotechar,
            quoting=self.quoting,
        )
        
        writer.writeheader()
        for row in data:
            # Convert values to strings
            row_str = {}
            for key, value in row.items():
                if value is None:
                    row_str[key] = ""
                elif isinstance(value, (datetime, date)):
                    row_str[key] = value.isoformat()
                elif isinstance(value, (int, float, bool)):
                    row_str[key] = str(value)
                elif isinstance(value, (list, dict)):
                    row_str[key] = json.dumps(value, cls=CustomJSONEncoder)
                else:
                    row_str[key] = str(value)
            writer.writerow(row_str)
        
        return buffer.getvalue().encode('utf-8')
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize CSV bytes to data."""
        # Parse CSV
        buffer = StringIO(data.decode('utf-8'))
        reader = csv.DictReader(
            buffer,
            delimiter=self.delimiter,
            quotechar=self.quotechar,
        )
        
        result = []
        for row in reader:
            # Convert empty strings to None
            processed_row = {}
            for key, value in row.items():
                if value == "":
                    processed_row[key] = None
                else:
                    # Try to parse as JSON
                    try:
                        processed_row[key] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        # Keep as string
                        processed_row[key] = value
            result.append(processed_row)
        
        return result


class ExcelSerializer(Serializer):
    """Excel serialization using pandas."""
    
    def __init__(
        self,
        sheet_name: str = "Sheet1",
        index: bool = False,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.sheet_name = sheet_name
        self.index = index
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to Excel bytes."""
        try:
            # Convert data to DataFrame
            if isinstance(data, pd.DataFrame):
                df = data
            elif isinstance(data, list):
                df = pd.DataFrame(data)
            elif isinstance(data, dict):
                df = pd.DataFrame([data])
            else:
                raise SerializationError(f"Cannot convert {type(data)} to DataFrame")
            
            # Write to Excel buffer
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(
                    writer,
                    sheet_name=self.sheet_name,
                    index=self.index,
                    **kwargs,
                )
            
            return buffer.getvalue()
            
        except Exception as e:
            raise SerializationError(f"Excel serialization failed: {e}")
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize Excel bytes to data."""
        try:
            buffer = BytesIO(data)
            df = pd.read_excel(buffer, **kwargs)
            
            # Convert to list of dicts
            return df.to_dict('records')
            
        except Exception as e:
            raise DeserializationError(f"Excel deserialization failed: {e}")


class ParquetSerializer(Serializer):
    """Parquet serialization for columnar data."""
    
    def __init__(
        self,
        compression: str = "snappy",
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.compression = compression
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data to Parquet bytes."""
        try:
            # Convert data to DataFrame
            if isinstance(data, pd.DataFrame):
                df = data
            elif isinstance(data, list):
                df = pd.DataFrame(data)
            elif isinstance(data, dict):
                df = pd.DataFrame([data])
            else:
                raise SerializationError(f"Cannot convert {type(data)} to DataFrame")
            
            # Convert to PyArrow Table
            table = pa.Table.from_pandas(df)
            
            # Write to buffer
            buffer = BytesIO()
            pq.write_table(table, buffer, compression=self.compression)
            
            return buffer.getvalue()
            
        except Exception as e:
            raise SerializationError(f"Parquet serialization failed: {e}")
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize Parquet bytes to data."""
        try:
            buffer = BytesIO(data)
            table = pq.read_table(buffer)
            
            # Convert to DataFrame
            df = table.to_pandas()
            
            # Convert to list of dicts
            return df.to_dict('records')
            
        except Exception as e:
            raise DeserializationError(f"Parquet deserialization failed: {e}")


class CompressionWrapper:
    """Wrapper for compression algorithms."""
    
    COMPRESSION_ALGORITHMS = {
        "gzip": gzip,
        "zlib": zlib,
        "brotli": brotli,
        "none": None,
    }
    
    def __init__(self, algorithm: str = "gzip", level: Optional[int] = None):
        self.algorithm = algorithm
        self.level = level
        
        if algorithm not in self.COMPRESSION_ALGORITHMS:
            raise ValueError(f"Unsupported compression algorithm: {algorithm}")
    
    def compress(self, data: bytes) -> bytes:
        """Compress data."""
        if self.algorithm == "none" or not data:
            return data
        
        if self.algorithm == "gzip":
            compressobj = gzip.compress
        elif self.algorithm == "zlib":
            compressobj = zlib.compress
        elif self.algorithm == "brotli":
            compressobj = brotli.compress
        else:
            raise CompressionError(f"Unknown algorithm: {self.algorithm}")
        
        try:
            if self.level is not None:
                return compressobj(data, level=self.level)
            return compressobj(data)
        except Exception as e:
            raise CompressionError(f"Compression failed: {e}")
    
    def decompress(self, data: bytes) -> bytes:
        """Decompress data."""
        if self.algorithm == "none" or not data:
            return data
        
        if self.algorithm == "gzip":
            decompressobj = gzip.decompress
        elif self.algorithm == "zlib":
            decompressobj = zlib.decompress
        elif self.algorithm == "brotli":
            decompressobj = brotli.decompress
        else:
            raise CompressionError(f"Unknown algorithm: {self.algorithm}")
        
        try:
            return decompressobj(data)
        except Exception as e:
            raise CompressionError(f"Decompression failed: {e}")


class EncryptionWrapper:
    """Wrapper for encryption during serialization."""
    
    def __init__(self, key: Optional[bytes] = None):
        self.key = key or Fernet.generate_key()
        self.fernet = Fernet(self.key)
    
    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data."""
        try:
            return self.fernet.encrypt(data)
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {e}")
    
    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data."""
        try:
            return self.fernet.decrypt(data)
        except InvalidToken as e:
            raise EncryptionError(f"Decryption failed: invalid token")
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}")


class ValidatedSerializer(Serializer):
    """Serializer with Pydantic validation."""
    
    def __init__(
        self,
        model: Type[BaseModel],
        inner_serializer: Serializer,
        validate_on_deserialize: bool = True,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.model = model
        self.inner_serializer = inner_serializer
        self.validate_on_deserialize = validate_on_deserialize
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data with validation."""
        # Validate input
        if not isinstance(data, self.model):
            try:
                data = self.model(**data)
            except ValidationError as e:
                raise SerializationError(f"Validation failed: {e}")
        
        # Convert to dict for serialization
        data_dict = data.model_dump(mode="json")
        
        # Serialize with inner serializer
        return self.inner_serializer.serialize(data_dict, **kwargs)
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize data with validation."""
        # Deserialize with inner serializer
        data_dict = self.inner_serializer.deserialize(data, **kwargs)
        
        if self.validate_on_deserialize:
            # Validate with Pydantic
            try:
                return self.model(**data_dict)
            except ValidationError as e:
                raise DeserializationError(f"Validation failed: {e}")
        
        return data_dict


class VersionedSerializer(Serializer):
    """Serializer with version compatibility."""
    
    def __init__(
        self,
        name: str,
        current_version: int,
        serializer_factory: Callable[[int], Serializer],
        schema_registry: Optional[SchemaRegistry] = None,
        circular_ref_handler: Optional[CircularReferenceHandler] = None,
    ):
        super().__init__(circular_ref_handler)
        self.name = name
        self.current_version = current_version
        self.serializer_factory = serializer_factory
        self.schema_registry = schema_registry or SchemaRegistry()
    
    def serialize(self, data: Any, **kwargs) -> bytes:
        """Serialize data with version info."""
        # Get serializer for current version
        serializer = self.serializer_factory(self.current_version)
        
        # Add version info to data
        versioned_data = {
            "__version__": self.current_version,
            "__name__": self.name,
            "data": data,
        }
        
        # Serialize
        serialized = serializer.serialize(versioned_data, **kwargs)
        
        # Add header with version
        header = f"VER{self.current_version:04d}".encode('utf-8')
        return header + serialized
    
    def deserialize(self, data: bytes, **kwargs) -> Any:
        """Deserialize data with version handling."""
        # Extract version from header
        if len(data) < 7:  # "VER" + 4 digits
            raise DeserializationError("Invalid versioned data format")
        
        header = data[:7].decode('utf-8')
        if not header.startswith("VER"):
            raise DeserializationError("Invalid version header")
        
        version = int(header[3:])
        data_without_header = data[7:]
        
        # Get serializer for data version
        serializer = self.serializer_factory(version)
        
        # Deserialize
        versioned_data = serializer.deserialize(data_without_header, **kwargs)
        
        # Extract data
        if not isinstance(versioned_data, dict) or "data" not in versioned_data:
            raise DeserializationError("Invalid versioned data structure")
        
        data = versioned_data["data"]
        
        # Migrate data if needed
        if version != self.current_version and self.schema_registry:
            try:
                data = self.schema_registry.migrate_data(
                    self.name,
                    data,
                    version,
                    self.current_version,
                )
            except VersionCompatibilityError:
                # Could not migrate, return as-is
                warnings.warn(
                    f"Could not migrate {self.name} from v{version} to v{self.current_version}",
                    RuntimeWarning,
                )
        
        return data


class SerializationManager:
    """Main manager for all serialization formats."""
    
    def __init__(self):
        self.serializers: Dict[str, Serializer] = {}
        self.compression_wrapper: Optional[CompressionWrapper] = None
        self.encryption_wrapper: Optional[EncryptionWrapper] = None
        self.schema_registry = SchemaRegistry()
        
        # Register default serializers
        self._register_default_serializers()
    
    def _register_default_serializers(self) -> None:
        """Register default serializers."""
        self.serializers["json"] = JSONSerializer(backend="orjson")
        self.serializers["yaml"] = YAMLSerializer(safe_load=True)
        self.serializers["msgpack"] = MessagePackSerializer()
        self.serializers["csv"] = CSVSerializer()
        
        # Excel and Parquet require pandas
        try:
            import pandas as pd
            self.serializers["excel"] = ExcelSerializer()
            self.serializers["parquet"] = ParquetSerializer()
        except ImportError:
            pass
    
    def register_serializer(self, name: str, serializer: Serializer) -> None:
        """Register a custom serializer."""
        self.serializers[name] = serializer
    
    def get_serializer(self, name: str) -> Serializer:
        """Get serializer by name."""
        if name not in self.serializers:
            raise ValueError(f"Unknown serializer: {name}")
        return self.serializers[name]
    
    def set_compression(self, algorithm: str = "gzip", level: Optional[int] = None) -> None:
        """Set compression algorithm."""
        self.compression_wrapper = CompressionWrapper(algorithm, level)
    
    def set_encryption(self, key: Optional[bytes] = None) -> None:
        """Set encryption key."""
        self.encryption_wrapper = EncryptionWrapper(key)
    
    def serialize(
        self,
        data: Any,
        format: str = "json",
        compress: bool = False,
        encrypt: bool = False,
        **kwargs,
    ) -> bytes:
        """
        Serialize data with optional compression and encryption.
        
        Args:
            data: Data to serialize
            format: Serialization format
            compress: Whether to compress
            encrypt: Whether to encrypt
            **kwargs: Additional arguments for serializer
            
        Returns:
            Serialized bytes
        """
        # Get serializer
        serializer = self.get_serializer(format)
        
        # Serialize
        serialized = serializer.serialize(data, **kwargs)
        
        # Compress if requested
        if compress and self.compression_wrapper:
            serialized = self.compression_wrapper.compress(serialized)
        
        # Encrypt if requested
        if encrypt and self.encryption_wrapper:
            serialized = self.encryption_wrapper.encrypt(serialized)
        
        return serialized
    
    def deserialize(
        self,
        data: bytes,
        format: str = "json",
        compressed: bool = False,
        encrypted: bool = False,
        **kwargs,
    ) -> Any:
        """
        Deserialize data with optional decompression and decryption.
        
        Args:
            data: Data to deserialize
            format: Serialization format
            compressed: Whether data is compressed
            encrypted: Whether data is encrypted
            **kwargs: Additional arguments for serializer
            
        Returns:
            Deserialized data
        """
        # Decrypt if needed
        if encrypted and self.encryption_wrapper:
            try:
                data = self.encryption_wrapper.decrypt(data)
            except EncryptionError as e:
                raise DeserializationError(f"Decryption failed: {e}")
        
        # Decompress if needed
        if compressed and self.compression_wrapper:
            try:
                data = self.compression_wrapper.decompress(data)
            except CompressionError as e:
                raise DeserializationError(f"Decompression failed: {e}")
        
        # Get serializer
        serializer = self.get_serializer(format)
        
        # Deserialize
        return serializer.deserialize(data, **kwargs)
    
    def benchmark(
        self,
        data: Any,
        formats: Optional[List[str]] = None,
        iterations: int = 1000,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Benchmark serialization performance.
        
        Args:
            data: Data to benchmark
            formats: Formats to benchmark (default: all)
            iterations: Number of iterations
            
        Returns:
            Benchmark results
        """
        if formats is None:
            formats = list(self.serializers.keys())
        
        results = {}
        
        for format_name in formats:
            if format_name not in self.serializers:
                continue
            
            serializer = self.serializers[format_name]
            
            # Warm up
            for _ in range(10):
                serializer.serialize(data)
                serializer.deserialize(serializer.serialize(data))
            
            # Benchmark serialization
            serialization_times = []
            for _ in range(iterations):
                start = time.perf_counter()
                serialized = serializer.serialize(data)
                end = time.perf_counter()
                serialization_times.append(end - start)
            
            # Benchmark deserialization
            serialized_data = serializer.serialize(data)
            deserialization_times = []
            for _ in range(iterations):
                start = time.perf_counter()
                serializer.deserialize(serialized_data)
                end = time.perf_counter()
                deserialization_times.append(end - start)
            
            # Calculate statistics
            def calculate_stats(times: List[float]) -> Dict[str, float]:
                return {
                    "min": min(times) * 1000,  # ms
                    "max": max(times) * 1000,
                    "mean": (sum(times) / len(times)) * 1000,
                    "p50": sorted(times)[len(times) // 2] * 1000,
                    "p95": sorted(times)[int(len(times) * 0.95)] * 1000,
                    "p99": sorted(times)[int(len(times) * 0.99)] * 1000,
                }
            
            results[format_name] = {
                "size_bytes": len(serialized_data),
                "compression_ratio": len(serialized_data) / len(str(data).encode('utf-8')),
                "serialization": calculate_stats(serialization_times),
                "deserialization": calculate_stats(deserialization_times),
            }
        
        return results


# Global serialization manager
_serialization_manager: Optional[SerializationManager] = None


def get_serialization_manager() -> SerializationManager:
    """Get or create global serialization manager."""
    global _serialization_manager
    
    if _serialization_manager is None:
        _serialization_manager = SerializationManager()
    
    return _serialization_manager


# Convenience functions
def serialize(
    data: Any,
    format: str = "json",
    compress: bool = False,
    encrypt: bool = False,
    **kwargs,
) -> bytes:
    """Convenience function for serialization."""
    manager = get_serialization_manager()
    return manager.serialize(data, format, compress, encrypt, **kwargs)


def deserialize(
    data: bytes,
    format: str = "json",
    compressed: bool = False,
    encrypted: bool = False,
    **kwargs,
) -> Any:
    """Convenience function for deserialization."""
    manager = get_serialization_manager()
    return manager.deserialize(data, format, compressed, encrypted, **kwargs)


def benchmark_serialization(
    data: Any,
    formats: Optional[List[str]] = None,
    iterations: int = 1000,
) -> Dict[str, Dict[str, Any]]:
    """Convenience function for benchmarking."""
    manager = get_serialization_manager()
    return manager.benchmark(data, formats, iterations)


# Export public API
__all__ = [
    # Main classes
    "SerializationManager",
    "Serializer",
    "JSONSerializer",
    "YAMLSerializer",
    "MessagePackSerializer",
    "AvroSerializer",
    "ProtobufSerializer",
    "CSVSerializer",
    "ExcelSerializer",
    "ParquetSerializer",
    "ValidatedSerializer",
    "VersionedSerializer",
    
    # Wrappers
    "CompressionWrapper",
    "EncryptionWrapper",
    
    # Utilities
    "CustomJSONEncoder",
    "CircularReferenceHandler",
    "SchemaRegistry",
    
    # Exceptions
    "SerializationError",
    "DeserializationError",
    "SchemaValidationError",
    "VersionCompatibilityError",
    "CompressionError",
    "EncryptionError",
    
    # Convenience functions
    "get_serialization_manager",
    "serialize",
    "deserialize",
    "benchmark_serialization",
    
    # Streaming
    "SerializationStream",
    "DeserializationStream",
]