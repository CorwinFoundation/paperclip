# Development verification

The vendor installation path is container-only. Contributors may run the locked
test suite from source in an isolated engineering environment:

```sh
python3 -m pip install --require-hashes -r requirements.txt
./run_all_tests.sh
```

This workflow is not part of vendor installation.
