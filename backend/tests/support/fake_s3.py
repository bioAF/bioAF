"""A small in-memory S3 that honours the conditional headers bioAF relies on.

``IfNoneMatch="*"`` on CopyObject and CompleteMultipartUpload, ``CopySourceIfMatch`` and
``VersionId`` on the source, ``IfMatch``/``VersionId`` on HeadObject and DeleteObject. A condition
that does not hold raises the ``ClientError`` S3 returns (412 PreconditionFailed, 404) and changes
nothing; aborted multipart uploads never publish.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

_ids = itertools.count(1)


def _error(code: str, status: int, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op)


@dataclass
class S3Object:
    data: bytes
    metadata: dict
    content_type: str | None
    version_id: str | None
    etag: str


@dataclass
class FakeS3:
    versioning: bool = True
    objects: dict[tuple[str, str], S3Object] = field(default_factory=dict)
    noncurrent: list[tuple[str, str, S3Object]] = field(default_factory=list)
    uploads: dict[str, dict] = field(default_factory=dict)
    aborted: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    before_publish: object = None
    # Make the next N deletes fail with a 500.
    fail_deletes: int = 0

    def put(self, bucket, key, data: bytes, metadata=None, content_type=None, multipart=False) -> S3Object:
        etag = f'"{hashlib.md5(data).hexdigest()}{"-2" if multipart else ""}"'
        old = self.objects.get((bucket, key))
        if old is not None and self.versioning:
            self.noncurrent.append((bucket, key, old))
        obj = S3Object(data, dict(metadata or {}), content_type, f"v{next(_ids)}" if self.versioning else None, etag)
        self.objects[(bucket, key)] = obj
        return obj

    def _head(self, obj: S3Object) -> dict:
        resp = {
            "ContentLength": len(obj.data),
            "ETag": obj.etag,
            "Metadata": dict(obj.metadata),
            "ServerSideEncryption": "AES256",
        }
        if obj.content_type:
            resp["ContentType"] = obj.content_type
        if obj.version_id:
            resp["VersionId"] = obj.version_id
        return resp

    def _find(self, bucket, key, version_id=None) -> S3Object | None:
        obj = self.objects.get((bucket, key))
        if version_id is None:
            return obj
        if obj is not None and obj.version_id == version_id:
            return obj
        for b, k, old in self.noncurrent:
            if (b, k) == (bucket, key) and old.version_id == version_id:
                return old
        return None

    # -- client surface ---------------------------------------------------------------------

    def head_object(self, Bucket, Key, VersionId=None, IfMatch=None):
        self.calls.append(("head_object", {"Key": Key, "VersionId": VersionId, "IfMatch": IfMatch}))
        obj = self._find(Bucket, Key, VersionId)
        if obj is None:
            raise _error("404", 404, "HeadObject")
        if IfMatch is not None and IfMatch != obj.etag:
            raise _error("412", 412, "HeadObject")
        return self._head(obj)

    def _source(self, CopySource, CopySourceIfMatch, op):
        src = self._find(CopySource["Bucket"], CopySource["Key"], CopySource.get("VersionId"))
        if src is None:
            raise _error("NoSuchKey", 404, op)
        if CopySourceIfMatch is not None and CopySourceIfMatch != src.etag:
            raise _error("PreconditionFailed", 412, op)
        return src

    def copy_object(self, Bucket, Key, CopySource, CopySourceIfMatch=None, IfNoneMatch=None, **kw):
        self.calls.append(("copy_object", {"Key": Key, "IfNoneMatch": IfNoneMatch, **kw}))
        src = self._source(CopySource, CopySourceIfMatch, "CopyObject")
        if callable(self.before_publish):
            self.before_publish()
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise _error("PreconditionFailed", 412, "CopyObject")
        self.put(Bucket, Key, src.data, kw.get("Metadata", src.metadata), kw.get("ContentType", src.content_type))
        return {}

    def create_multipart_upload(self, Bucket, Key, Metadata=None, ContentType=None, **kw):
        upload_id = f"mpu-{next(_ids)}"
        self.uploads[upload_id] = {"key": (Bucket, Key), "parts": {}, "metadata": Metadata, "ct": ContentType}
        return {"UploadId": upload_id}

    def upload_part_copy(self, Bucket, Key, UploadId, PartNumber, CopySource, CopySourceIfMatch, CopySourceRange):
        src = self._source(CopySource, CopySourceIfMatch, "UploadPartCopy")
        start, end = (int(x) for x in CopySourceRange.removeprefix("bytes=").split("-"))
        self.uploads[UploadId]["parts"][PartNumber] = src.data[start : end + 1]
        return {"CopyPartResult": {"ETag": f'"p{PartNumber}"'}}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch=None):
        self.calls.append(("complete_multipart_upload", {"Key": Key, "IfNoneMatch": IfNoneMatch}))
        upload = self.uploads[UploadId]
        if callable(self.before_publish):
            self.before_publish()
        if IfNoneMatch == "*" and (Bucket, Key) in self.objects:
            raise _error("PreconditionFailed", 412, "CompleteMultipartUpload")
        data = b"".join(upload["parts"][p["PartNumber"]] for p in MultipartUpload["Parts"])
        self.put(Bucket, Key, data, upload["metadata"], upload["ct"], multipart=True)
        del self.uploads[UploadId]
        return {}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append(UploadId)
        self.uploads.pop(UploadId, None)

    def delete_object(self, Bucket, Key, VersionId=None, IfMatch=None):
        """S3's versioned semantics: permanently deleting the current version makes the newest
        noncurrent version current again (readable at the key); deleting a noncurrent version
        removes only it; a delete without a version in a versioned bucket hides the object behind
        a delete marker."""
        self.calls.append(("delete_object", {"Key": Key, "VersionId": VersionId, "IfMatch": IfMatch}))
        if self.fail_deletes:
            self.fail_deletes -= 1
            raise _error("InternalError", 500, "DeleteObject")
        obj = self.objects.get((Bucket, Key))
        if VersionId is not None:
            if obj is not None and obj.version_id == VersionId:
                del self.objects[(Bucket, Key)]
                older = [i for i, (b, k, _o) in enumerate(self.noncurrent) if (b, k) == (Bucket, Key)]
                if older:
                    self.objects[(Bucket, Key)] = self.noncurrent.pop(older[-1])[2]
            else:
                self.noncurrent = [
                    (b, k, o)
                    for b, k, o in self.noncurrent
                    if not ((b, k) == (Bucket, Key) and o.version_id == VersionId)
                ]
            return {}
        if obj is None:
            return {}
        if IfMatch is not None and IfMatch != obj.etag:
            raise _error("PreconditionFailed", 412, "DeleteObject")
        if self.versioning:
            self.noncurrent.append((Bucket, Key, obj))
        del self.objects[(Bucket, Key)]
        return {}
