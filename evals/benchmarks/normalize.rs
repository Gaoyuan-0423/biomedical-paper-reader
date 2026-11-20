/// Benchmark-only implementation for ASCII input.
///
/// # Safety
/// The caller supplies non-overlapping buffers valid for `len` bytes, with the
/// destination writable. The return value is the number of initialized bytes.
#[no_mangle]
pub unsafe extern "C" fn normalize_ascii(src: *const u8, len: usize, dest: *mut u8) -> usize {
    let mut written = 0;
    for &c in std::slice::from_raw_parts(src, len) {
        if !matches!(c, 9..=13 | 28..=32) {
            dest.add(written).write(c.to_ascii_lowercase());
            written += 1;
        }
    }
    written
}
