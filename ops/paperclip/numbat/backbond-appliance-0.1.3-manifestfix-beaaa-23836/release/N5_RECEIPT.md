# BEAAA-21476 appliance 0.1.3 successor N=5 controlled package receipt

Exact image candidate was executed five times with a read-only root filesystem,
UID 10001, all capabilities dropped, `no-new-privileges`, 1 CPU, 512 MiB memory,
128 PIDs, and bounded tmpfs mounts.

Every run returned exit 0, empty stderr, and `ALL GATES GREEN`. Stdout hashes differ
only because synthetic tests print newly allocated `/tmp` paths.

| Run | stdout SHA-256 | stderr SHA-256 |
| --- | --- | --- |
| 1 | `68fad7b03dd19d4039a63a229a7fcf115972dc5931c11860923e18b348a41b34` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 2 | `2a3b101f957ee945468d03b6bf3c9f5bfde6eead0ee4e14862a404470530847b` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 3 | `8ebf16871121b63a412e7a5005d87a80bd417d55a1ca52e19141758c4eaf2d0d` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 4 | `cd37d8a80cb6b69ed37544b2f098f870b9e85a49ebb0abc7d5d7c3f4048fc283` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 5 | `0dfb268c7bb66865744c0780c93f4521cd575c6726825a1641b2b1a281a14bf1` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Scanner score impact is zero: the appliance package imports no Scanner scoring
module and does not change rubric, corpus, probe, judge, or score output. Controlled
golden delta: `0.0000` on total and every pillar.
