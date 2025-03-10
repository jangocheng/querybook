import boto3
import botocore

from env import QuerybookSettings
from lib.utils.utf8 import split_by_last_invalid_utf8_char

from .common import ChunkReader, FileDoesNotExist


class MultiPartUploader(object):
    def __init__(self, bucket_name, key):
        self._bucket_name = bucket_name
        self._key = key
        self._s3 = boto3.client("s3")
        self._mpu = self._s3.create_multipart_upload(Bucket=bucket_name, Key=key)
        self._parts = []
        self._part_number = 1

        self.chunk = []
        self.chunk_datasize = 0
        self.is_first_upload = True

    def _upload_part(self, body):
        if self._part_number > QuerybookSettings.STORE_MAX_UPLOAD_CHUNK_NUM:
            return

        part = self._s3.upload_part(
            Bucket=self._bucket_name,
            Key=self._key,
            PartNumber=self._part_number,
            UploadId=self._mpu["UploadId"],
            Body=body,
        )

        self._parts.append(
            {"PartNumber": self._part_number, "ETag": part["ETag"].replace('"', "")}
        )
        self._part_number += 1

    def write(self, string: str) -> bool:
        """Write a string to upload

        Arguments:
            string {str} -- the string to upload

        Returns:
            bool -- Whether or not the upload is successful
        """
        if self._part_number > QuerybookSettings.STORE_MAX_UPLOAD_CHUNK_NUM:
            return False

        self.chunk.append(string)
        self.chunk_datasize += len(string)
        if self.chunk_datasize > QuerybookSettings.STORE_MIN_UPLOAD_CHUNK_SIZE:
            self._upload_part("".join(self.chunk))
            self.chunk = []
            self.chunk_datasize = 0
        return True

    def write_line(self, string: str):
        self.write(string + "\n")

    def complete(self):
        if len(self.chunk) > 0:
            self._upload_part("".join(self.chunk))
        self._s3.complete_multipart_upload(
            Bucket=self._bucket_name,
            Key=self._key,
            UploadId=self._mpu["UploadId"],
            MultipartUpload={"Parts": self._parts},
        )


class S3KeySigner(object):
    def __init__(self, bucket_name):
        self._bucket_name = bucket_name
        self._s3 = boto3.client("s3")
        self._bucket = boto3.resource("s3").Bucket(bucket_name)

    def generate_presigned_url(
        self, key, method="get_object", expires_in=86400, params={}
    ):
        params.update({"Bucket": self._bucket_name, "Key": key})

        # Check if file exists
        objects = list(self._bucket.objects.filter(Prefix=key))
        if len(objects) > 0 and objects[0].key == key:
            url = self._s3.generate_presigned_url(
                ClientMethod=method, Params=params, ExpiresIn=expires_in
            )
            return url
        return None


class S3FileReader(ChunkReader):
    def __init__(
        self,
        bucket_name,
        key,
        read_size=QuerybookSettings.STORE_READ_SIZE,
        max_read_size=QuerybookSettings.STORE_MAX_READ_SIZE,
    ):
        self._bucket_name = bucket_name
        self._key = key
        self._left_over_bytes = b""

        super(S3FileReader, self).__init__(read_size, max_read_size)

        # Now connect to s3 using boto3
        try:
            self._s3 = boto3.resource("s3")
            self._object = self._s3.Object(self._bucket_name, key)
            self._body = self._object.get()["Body"]
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileDoesNotExist(
                    "{}/{} does not exist".format(self._bucket_name, key)
                )
            else:
                raise e

    def read(self):
        raw = self._left_over_bytes + self._body.read(self._read_size)
        valid_raw, self._left_over_bytes = split_by_last_invalid_utf8_char(raw)
        return valid_raw.decode("utf-8")
