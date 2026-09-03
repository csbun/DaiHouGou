# GuDuck

GuDuck is a local-network childcare application that observes camera events and
responds through paired speakers without sending or retaining camera imagery.

## Language

**Visual Rule**:
A per-camera rule that turns a locally observed visual event into a recorded action.

**Person Entry Welcome Rule**:
The visual rule that announces a welcome when a person enters a camera's view.
_Avoid_: Person detection rule, welcome switch

**Object Category Detection**:
Detection of supported, predefined categories of objects in a camera's view. It does
not identify a particular object, accept arbitrary text categories, or learn from
user-provided examples. Detection of picture-book illustrations is best effort.
_Avoid_: General object recognition, object identification

**Object Category Announcement Rule**:
The visual rule that announces and records supported non-person object categories
after a meaningful change in a camera's view. It is independent of the Person Entry
Welcome Rule, may be enabled at the same time, and analyzes its first available view
after activation or recovery rather than treating it as calibration.
_Avoid_: Object entry rule, object recognition feature, animal rule

**Supported Category**:
A predefined non-person object category that the Object Category Announcement Rule
may announce. Its fixed vocabulary comes from the active detector adapter; all of its
categories are eligible without per-camera selection and are published in a read-only
list with an English label and a maintained display name.
_Avoid_: Target category, object name, prompt, custom class

**Object Detector Selection**:
The single detector adapter used by every Object Category Announcement Rule. It is
selected globally in the management interface, persists across restarts, and defaults
to NanoDet. A new selection takes effect only after its model loads successfully; an
unavailable or failed selection leaves the current detector and stored choice intact.
_Avoid_: Per-camera model, detector environment override

**Scene Change**:
A meaningful visual change, such as turning a picture-book page, that makes the
current view worth analyzing again. It does not imply that the new view has remained
stable, while camera noise or a small exposure fluctuation is not a Scene Change.
_Avoid_: Motion event, object entry

**Object Announcement**:
A spoken, phrase-free list of at most three distinct Supported Category names from
one analyzed view, ordered by detection confidence, such as "cat, ball, dog"; a newer
one supersedes an older one from the same camera that has not started playing. It is
recorded as one outcome with per-category confidence, stays silent rather than saying
"unknown", and expires after three seconds or when its rule or speaker pairing changes.
_Avoid_: Description, caption, story

**Picture-book Page**:
The physical page presented to a fixed camera as the subject of Object Category
Detection. A book occupying most of the view is presentation media and is not
announced, while a smaller book depicted within the page remains eligible.
_Avoid_: Stored page, captured page

**Validation Corpus**:
A temporary, local-only set of person-free Picture-book Page images used to evaluate
the object detector on its target hardware. It is never committed or uploaded and is
deleted after aggregate validation results are recorded.
_Avoid_: Runtime capture archive, training data
