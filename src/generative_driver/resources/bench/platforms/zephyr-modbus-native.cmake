# Keep generated build-directory names out of the native runner's debug records.
# Application and protocol sources remain upstream bytes.
target_compile_options(native_simulator INTERFACE
    "-ffile-prefix-map=${APPLICATION_BINARY_DIR}=/build")
