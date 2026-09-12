from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ScaledObjectRef(_message.Message):
    __slots__ = ("name", "namespace", "scalerMetadata")
    class ScalerMetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    SCALERMETADATA_FIELD_NUMBER: _ClassVar[int]
    name: str
    namespace: str
    scalerMetadata: _containers.ScalarMap[str, str]
    def __init__(self, name: _Optional[str] = ..., namespace: _Optional[str] = ..., scalerMetadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class IsActiveResponse(_message.Message):
    __slots__ = ("result",)
    RESULT_FIELD_NUMBER: _ClassVar[int]
    result: bool
    def __init__(self, result: _Optional[bool] = ...) -> None: ...

class GetMetricSpecResponse(_message.Message):
    __slots__ = ("metricSpecs",)
    METRICSPECS_FIELD_NUMBER: _ClassVar[int]
    metricSpecs: _containers.RepeatedCompositeFieldContainer[MetricSpec]
    def __init__(self, metricSpecs: _Optional[_Iterable[_Union[MetricSpec, _Mapping]]] = ...) -> None: ...

class MetricSpec(_message.Message):
    __slots__ = ("metricName", "targetSize", "targetSizeFloat")
    METRICNAME_FIELD_NUMBER: _ClassVar[int]
    TARGETSIZE_FIELD_NUMBER: _ClassVar[int]
    TARGETSIZEFLOAT_FIELD_NUMBER: _ClassVar[int]
    metricName: str
    targetSize: int
    targetSizeFloat: float
    def __init__(self, metricName: _Optional[str] = ..., targetSize: _Optional[int] = ..., targetSizeFloat: _Optional[float] = ...) -> None: ...

class GetMetricsRequest(_message.Message):
    __slots__ = ("scaledObjectRef", "metricName")
    SCALEDOBJECTREF_FIELD_NUMBER: _ClassVar[int]
    METRICNAME_FIELD_NUMBER: _ClassVar[int]
    scaledObjectRef: ScaledObjectRef
    metricName: str
    def __init__(self, scaledObjectRef: _Optional[_Union[ScaledObjectRef, _Mapping]] = ..., metricName: _Optional[str] = ...) -> None: ...

class GetMetricsResponse(_message.Message):
    __slots__ = ("metricValues",)
    METRICVALUES_FIELD_NUMBER: _ClassVar[int]
    metricValues: _containers.RepeatedCompositeFieldContainer[MetricValue]
    def __init__(self, metricValues: _Optional[_Iterable[_Union[MetricValue, _Mapping]]] = ...) -> None: ...

class MetricValue(_message.Message):
    __slots__ = ("metricName", "metricValue", "metricValueFloat")
    METRICNAME_FIELD_NUMBER: _ClassVar[int]
    METRICVALUE_FIELD_NUMBER: _ClassVar[int]
    METRICVALUEFLOAT_FIELD_NUMBER: _ClassVar[int]
    metricName: str
    metricValue: int
    metricValueFloat: float
    def __init__(self, metricName: _Optional[str] = ..., metricValue: _Optional[int] = ..., metricValueFloat: _Optional[float] = ...) -> None: ...
