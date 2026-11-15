# Local changes to third_party/LibAtrac9

`third_party/LibAtrac9` is vendored: upstream Thealexbarney/LibAtrac9 (`efca2e3`) committed in this
repository with these patches already applied. They record what differs from upstream, for the next
update of the library; `build.sh` checks that every one is in the tree and stops when one is missing
(`VENDORED_PATCHES`, `tests/test_vendored_patches.py`):

- `0001-...`: Basara 4 PC decoder hardening. A damaged ATRAC9 stream is rejected instead of reading
  past the decoder's tables: `band_extension.c` returns for quantization unit counts the band
  extension tables do not cover (13-20, 1-6 extension units), `huffCodes.c` reads no value from an
  unused codebook, and `unpack.c` reports `ERR_UNPACK_BAND_PARAMS_INVALID` for one.
