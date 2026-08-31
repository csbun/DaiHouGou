# Use separate serialized vision models

The Person Entry Welcome Rule keeps its lightweight continuously sampled person
detector, while the Object Category Announcement Rule uses a separate detector only
for changed views. Both detectors submit work to one serialized CPU inference
dispatcher. This accepts the memory and scheduling complexity of two models to avoid
running the heavier object detector continuously and to preserve independent rule
behavior.

The dispatcher keeps at most one latest-frame job for each camera and detector type
and serves those pairs fairly without an absolute rule priority. A detector failure
degrades only its dependent rule. Each camera retains one frame source: it may be
restarted at a higher validated resolution while the object rule is active, but a
second detector never causes duplicate video decoding.
