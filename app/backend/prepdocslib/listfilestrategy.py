import base64
import hashlib
import logging
import os
import re
import tempfile
from abc import ABC
from collections.abc import AsyncGenerator
from glob import glob
from typing import IO, Optional, Union

from azure.core.credentials_async import AsyncTokenCredential
from azure.storage.filedatalake.aio import (
    DataLakeServiceClient,
)

logger = logging.getLogger("scripts")


class File:
    """
    Represents a file stored either locally or in a data lake storage account
    """

    def __init__(self, content: IO, url: Optional[str] = None):
        self.content = content
        self.url = url

    def filename(self):
        return os.path.basename(self.content.name)

    def file_extension(self):
        return os.path.splitext(self.content.name)[1]

    def filename_to_id(self):
        filename_ascii = re.sub("[^0-9a-zA-Z_-]", "_", self.filename())
        filename_hash = base64.b16encode(self.filename().encode("utf-8")).decode("ascii")
        return f"file-{filename_ascii}-{filename_hash}"

    def close(self):
        if self.content:
            self.content.close()


class ListFileStrategy(ABC):
    """
    Abstract strategy for listing files that are located somewhere. For example, on a local computer or remotely in a storage account
    """

    async def list(self) -> AsyncGenerator[File, None]:
        if False:  # pragma: no cover - this is necessary for mypy to type check
            yield

    async def list_paths(self) -> AsyncGenerator[str, None]:
        if False:  # pragma: no cover - this is necessary for mypy to type check
            yield


class LocalListFileStrategy(ListFileStrategy):
    """
    Concrete strategy for listing files that are located in a local filesystem
    """

    def __init__(self, path_pattern: str):
        self.path_pattern = path_pattern

    async def list_paths(self) -> AsyncGenerator[str, None]:
        async for p in self._list_paths(self.path_pattern):
            yield p

    async def _list_paths(self, path_pattern: str) -> AsyncGenerator[str, None]:
        for path in glob(path_pattern):
            if os.path.isdir(path):
                async for p in self._list_paths(f"{path}/*"):
                    yield p
            else:
                # Only list files, not directories
                yield path

    async def list(self) -> AsyncGenerator[File, None]:
        async for path in self.list_paths():
            if not self.check_md5(path):
                yield File(content=open(path, mode="rb"))

    def check_md5(self, path: str) -> bool:
        # if filename ends in .md5 skip
        if path.endswith(".md5"):
            return True

        # if there is a file called .md5 in this directory, see if its updated
        stored_hash = None
        with open(path, "rb") as file:
            existing_hash = hashlib.md5(file.read()).hexdigest()
        hash_path = f"{path}.md5"
        if os.path.exists(hash_path):
            with open(hash_path, encoding="utf-8") as md5_f:
                stored_hash = md5_f.read()

        if stored_hash and stored_hash.strip() == existing_hash.strip():
            logger.info("Skipping %s, no changes detected.", path)
            return True

        # Write the hash
        with open(hash_path, "w", encoding="utf-8") as md5_f:
            md5_f.write(existing_hash)

        return False


class ADLSGen2ListFileStrategy(ListFileStrategy):
    """
    Concrete strategy for listing files that are located in a data lake storage account
    """

    def __init__(
        self,
        data_lake_storage_account: str,
        data_lake_filesystem: str,
        data_lake_path: str,
        credential: Union[AsyncTokenCredential, str],
    ):
        self.data_lake_storage_account = data_lake_storage_account
        self.data_lake_filesystem = data_lake_filesystem
        self.data_lake_path = data_lake_path
        self.credential = credential

    async def list_paths(self) -> AsyncGenerator[str, None]:
        async with DataLakeServiceClient(
            account_url=f"https://{self.data_lake_storage_account}.dfs.core.windows.net", credential=self.credential
        ) as service_client, service_client.get_file_system_client(self.data_lake_filesystem) as filesystem_client:
            async for path in filesystem_client.get_paths(path=self.data_lake_path, recursive=True):
                if path.is_directory:
                    continue

                yield path.name

    async def list(self) -> AsyncGenerator[File, None]:
        async with DataLakeServiceClient(
            account_url=f"https://{self.data_lake_storage_account}.dfs.core.windows.net", credential=self.credential
        ) as service_client, service_client.get_file_system_client(self.data_lake_filesystem) as filesystem_client:
            async for path in self.list_paths():
                temp_file_path = os.path.join(tempfile.gettempdir(), os.path.basename(path))
                try:
                    async with filesystem_client.get_file_client(path) as file_client:
                        with open(temp_file_path, "wb") as temp_file:
                            downloader = await file_client.download_file()
                            await downloader.readinto(temp_file)
                    yield File(content=open(temp_file_path, "rb"), url=file_client.url)
                except Exception as data_lake_exception:
                    logger.error(f"\tGot an error while reading {path} -> {data_lake_exception} --> skipping file")
                    try:
                        os.remove(temp_file_path)
                    except Exception as file_delete_exception:
                        logger.error(f"\tGot an error while deleting {temp_file_path} -> {file_delete_exception}")
