# Python 3.11 dependency locks

The API/Assistant Worker and attachment parse Worker have separate direct
inputs and hashed locks. Only api-worker.lock and parse-worker.lock are runtime
install sources. requirements.txt and requirements-parse-worker.txt remain
lock-only compatibility shims.

The supported target is CPython 3.11 on linux/amd64. Locks are compiled with
pip-tools==7.4.1 inside the immutable compiler image recorded in
compiler-image.txt; the compiler refuses tags, zero/dummy digests, another
platform, another Python minor, unhashed bootstrap, index directives, and
credential-like text.

Use the compile_requirements.py --write and --check modes, followed by the
clean-install mode for each target. The repository does not accept an
unreviewed secondary package index. The parse Worker is the reviewed
exception: its input and clean-install/build paths use PyTorch's official CPU
wheel index so a linux/amd64 install cannot silently pull CUDA distributions.
The index directive is deliberately absent from generated locks; every parse
install path supplies it explicitly.
