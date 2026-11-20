# 9. Interpretation talks to the device when there is no firmware

A generic run with no single firmware image used to skip interpretation and end "no driver emitted", even when the device answered on its own port. Either interpretation or probe could write the model from the device. We let the interpret worker do it, read-only, for that case only. Interpretation then stays the one stage that writes models, and probe and ground keep testing a model handed to them, so their differences still have a stage to send repairs back to.

The cost is that interpretation is no longer always device-free. Its device calls are limited to operations the worker declares as reads, whatever the run's grants. Every call is recorded as a probe's calls are. A run on a supplied device always has an image and never takes this path.
