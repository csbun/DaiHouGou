# Gate object inference on sampled scene changes

The Object Category Announcement Rule checks for meaningful scene changes at one
frame per second and runs expensive object inference immediately only when the view
has changed. This favors low CPU use and picture-book page-turn interaction over
continuous detection or a stabilization delay; analyzing or missing a transitional
frame is accepted as part of the rule's best-effort behavior. There is no fixed
post-detection suppression interval: every sampled changed view may be inferred,
while pending announcements from the same camera are coalesced so only the newest
one remains.
