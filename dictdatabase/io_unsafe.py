from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import orjson
import json
import zlib
import os
import hashlib
from pathlib import Path
from . import config, utils



@dataclass(frozen=True)
class PartialDict:
	prefix: str
	key: str
	value: str
	suffix: str


@dataclass(frozen=True)
class PartialFileHandle:
	db_name: str
	partial_dict: PartialDict
	indent_level: int
	indent_with: str
	index_data: dict


################################################################################
#### Reading
################################################################################


def read_file(db_name: str, as_bytes=False) -> str | bytes:
	"""
		Read the content of a db as a string, or as bytes if as_bytes=True.
		Reading works even when the config changes, so a compressed ddb file can
		also be read if compression is disabled, and vice versa.
	"""
	json_path, json_exists, ddb_path, ddb_exists = utils.db_paths(db_name)

	if json_exists and ddb_exists:
		raise FileExistsError(f"DB Inconsistency: \"{db_name}\" exists as .json and .ddb")

	if not json_exists and not ddb_exists:
		raise FileNotFoundError(f"DB \"{db_name}\" does not exist.")

	# Read from json file
	if json_exists:
		mode = "rb" if as_bytes else "r"
		with open(json_path, mode) as f:
			return f.read()
	# Read from compressed ddb file
	if ddb_exists:
		with open(ddb_path, "rb") as f:
			data_bytes = zlib.decompress(f.read())
			return data_bytes if as_bytes else data_bytes.decode()


def read(db_name: str) -> dict:
	"""
		Read the file at db_path from the configured storage directory.
		Make sure the file exists. If it does notnot a FileNotFoundError is
		raised.
	"""
	# Always use orjson to read the file, because it is faster
	return orjson.loads(read_file(db_name, as_bytes=True))



def read_index_file(db_name: str):
	path = f"{config.storage_directory}/.ddb/{db_name.replace('/', '___')}.index"
	Path(path).parent.mkdir(parents=True, exist_ok=True)
	if not os.path.exists(path):
		return {}
	with open(path, "rb") as f:
		return orjson.loads(f.read())


def write_index_file(index_data: dict, db_name: str, key, start_index, end_index, indent_level, indent_with, value_hash):
	path = f"{config.storage_directory}/.ddb/{db_name.replace('/', '___')}.index"
	index_data[key] = [start_index, end_index, indent_level, indent_with, value_hash]
	with open(path, "wb") as f:
		f.write(orjson.dumps(index_data))


def partial_read(db_name: str, key: str, as_handle=False) -> PartialFileHandle | dict:
	"""
		Partially read a key from a db.
		The key MUST be unique in the entire db, otherwise the behavior is undefined.
		This is a lot faster than reading the entire db, because it does not parse
		the entire file, but only the part <value> part of the <key>: <value> pair.

		If the key is not found, a `KeyError` is raised.
	"""

	data = read_file(db_name)

	# Search for key in the index file
	index_data = read_index_file(db_name)
	index = index_data.get(key, None)
	if index is not None:
		start_index, end_index, indent_level, indent_with, value_hash = index
		partial_str = data[start_index:end_index]
		partial_str_hash = hashlib.sha256(partial_str.encode()).hexdigest()
		if value_hash == partial_str_hash:
			partial_value = orjson.loads(partial_str)
			if not as_handle:
				return partial_value
			partial_dict = PartialDict(data[:start_index], key, partial_value, data[end_index:])
			return PartialFileHandle(db_name, partial_dict, indent_level, indent_with, index_data)

	# Not found in index file, search for key in the entire file
	json_key = f"\"{key}\":"
	json_key_start_index = utils.find_outermost_json_key_index(data, json_key)
	json_key_end_index = json_key_start_index + len(json_key)

	if json_key_start_index == -1:
		raise KeyError(f"Key \"{key}\" not found in db \"{db_name}\"")

	# Key found, now determine the bounds of the value
	space_after_semicolon = 1 if data[json_key_end_index] == " " else 0
	value_start_index = json_key_end_index + space_after_semicolon
	value_end_index = utils.seek_index_through_value(data, value_start_index)

	indent_level, indent_with  = utils.detect_indentation_in_json_string(data, json_key_start_index)
	partial_str = data[value_start_index:value_end_index]

	# Write key info to index file
	write_index_file(
		index_data,
		db_name,
		key,
		value_start_index,
		value_end_index,
		indent_level,
		indent_with,
		hashlib.sha256(partial_str.encode()).hexdigest()
	)

	partial_value = orjson.loads(partial_str)
	if not as_handle:
		return partial_value

	partial_dict = PartialDict(data[:value_start_index], key, partial_value, data[value_end_index:])
	return PartialFileHandle(db_name, partial_dict, indent_level, indent_with, index_data)



################################################################################
#### Writing
################################################################################


def write_dump(db_name: str, dump: str | bytes):
	"""
		Write the dump to the file of the db_path.
		If the db was compressed but now config.use_compression is False,
		remove the compressed file, and vice versa.
	"""
	json_path, json_exists, ddb_path, ddb_exists = utils.db_paths(db_name)
	# Write bytes or string to file
	if config.use_compression:
		write_path = ddb_path
		if json_exists:
			os.remove(json_path)
	else:
		write_path = json_path
		if ddb_exists:
			os.remove(ddb_path)

	if config.use_compression:
		dump = zlib.compress(dump if isinstance(dump, bytes) else dump.encode(), 1)

	# Write bytes or string to file
	open_mode = "wb" if isinstance(dump, bytes) else "w"
	with open(write_path, open_mode) as f:
		f.write(dump)


def write(db_name: str, db: dict):
	"""
		Write the dict db dumped as a json string
		to the file of the db_path.
	"""
	if config.use_orjson:
		option = orjson.OPT_INDENT_2 if config.indent else 0
		option |= orjson.OPT_SORT_KEYS if config.sort_keys else 0
		db_dump = orjson.dumps(db, option=option)
	else:
		db_dump = json.dumps(db, indent=config.indent, sort_keys=config.sort_keys)

	write_dump(db_name, db_dump)


def partial_write(pf: PartialFileHandle):
	"""
		Write a partial file handle to the db.
	"""
	if config.use_orjson:
		option = orjson.OPT_INDENT_2 if config.indent else 0
		option |= orjson.OPT_SORT_KEYS if config.sort_keys else 0
		partial_dump = orjson.dumps(pf.partial_dict.value, option=option)
		partial_dump = partial_dump.decode()
	else:
		partial_dump = json.dumps(pf.partial_dict.value, indent=config.indent, sort_keys=config.sort_keys)

	if pf.indent_level > 0 and pf.indent_with:
		partial_dump = partial_dump.replace("\n", "\n" + (pf.indent_level * pf.indent_with))

	write_index_file(
		pf.index_data,
		pf.db_name,
		pf.partial_dict.key,
		len(pf.partial_dict.prefix),
		len(pf.partial_dict.prefix) + len(partial_dump),
		pf.indent_level,
		pf.indent_with,
		hashlib.sha256(partial_dump.encode()).hexdigest()
	)

	dump = f"{pf.partial_dict.prefix}{partial_dump}{pf.partial_dict.suffix}"
	write_dump(pf.db_name, dump)
