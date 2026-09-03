import time
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from daihougou.vision.object_detector import (
    DetectedObject,
    Network,
    ObjectDetection,
)

# This order comes from the official yolo26n-objv1-150.pt checkpoint. It is not
# the same as the current Objects365.yaml class order.
_OBJECTS365_MODEL_CATEGORIES = (
    "person",
    "sneakers",
    "chair",
    "hat",
    "lamp",
    "bottle",
    "cabinet/shelf",
    "cup",
    "car",
    "glasses",
    "picture/frame",
    "desk",
    "handbag",
    "street lights",
    "book",
    "plate",
    "helmet",
    "leather shoes",
    "pillow",
    "glove",
    "potted plant",
    "bracelet",
    "flower",
    "tv",
    "storage box",
    "vase",
    "bench",
    "wine glass",
    "boots",
    "bowl",
    "dining table",
    "umbrella",
    "boat",
    "flag",
    "speaker",
    "trash bin/can",
    "stool",
    "backpack",
    "couch",
    "belt",
    "carpet",
    "basket",
    "towel/napkin",
    "slippers",
    "barrel/bucket",
    "coffee table",
    "suv",
    "toy",
    "tie",
    "bed",
    "traffic light",
    "pen/pencil",
    "microphone",
    "sandals",
    "canned",
    "necklace",
    "mirror",
    "faucet",
    "bicycle",
    "bread",
    "high heels",
    "ring",
    "van",
    "watch",
    "sink",
    "horse",
    "fish",
    "apple",
    "camera",
    "candle",
    "teddy bear",
    "cake",
    "motorcycle",
    "wild bird",
    "laptop",
    "knife",
    "traffic sign",
    "cell phone",
    "paddle",
    "truck",
    "cow",
    "power outlet",
    "clock",
    "drum",
    "fork",
    "bus",
    "hanger",
    "nightstand",
    "pot/pan",
    "sheep",
    "guitar",
    "traffic cone",
    "tea pot",
    "keyboard",
    "tripod",
    "hockey",
    "fan",
    "dog",
    "spoon",
    "blackboard/whiteboard",
    "balloon",
    "air conditioner",
    "cymbal",
    "mouse",
    "telephone",
    "pickup truck",
    "orange",
    "banana",
    "airplane",
    "luggage",
    "skis",
    "soccer",
    "trolley",
    "oven",
    "remote",
    "baseball glove",
    "paper towel",
    "refrigerator",
    "train",
    "tomato",
    "machinery vehicle",
    "tent",
    "shampoo/shower gel",
    "head phone",
    "lantern",
    "donut",
    "cleaning products",
    "sailboat",
    "tangerine",
    "pizza",
    "kite",
    "computer box",
    "elephant",
    "toiletries",
    "gas stove",
    "broccoli",
    "toilet",
    "stroller",
    "shovel",
    "baseball bat",
    "microwave",
    "skateboard",
    "surfboard",
    "surveillance camera",
    "gun",
    "life saver",
    "cat",
    "lemon",
    "liquid soap",
    "zebra",
    "duck",
    "sports car",
    "giraffe",
    "pumpkin",
    "piano",
    "stop sign",
    "radiator",
    "converter",
    "tissue",
    "carrot",
    "washing machine",
    "vent",
    "cookies",
    "cutting/chopping board",
    "tennis racket",
    "candy",
    "skating and skiing shoes",
    "scissors",
    "folder",
    "baseball",
    "strawberry",
    "bow tie",
    "pigeon",
    "pepper",
    "coffee machine",
    "bathtub",
    "snowboard",
    "suitcase",
    "grapes",
    "ladder",
    "pear",
    "american football",
    "basketball",
    "potato",
    "paint brush",
    "printer",
    "billiards",
    "fire hydrant",
    "goose",
    "projector",
    "sausage",
    "fire extinguisher",
    "extension cord",
    "facial mask",
    "tennis ball",
    "chopsticks",
    "electronic stove and gas stove",
    "pie",
    "frisbee",
    "kettle",
    "hamburger",
    "golf club",
    "cucumber",
    "clutch",
    "blender",
    "tong",
    "slide",
    "hot dog",
    "toothbrush",
    "facial cleanser",
    "mango",
    "deer",
    "egg",
    "violin",
    "marker",
    "ship",
    "chicken",
    "onion",
    "ice cream",
    "tape",
    "wheelchair",
    "plum",
    "bar soap",
    "scale",
    "watermelon",
    "cabbage",
    "router/modem",
    "golf ball",
    "pine apple",
    "crane",
    "fire truck",
    "peach",
    "cello",
    "notepaper",
    "tricycle",
    "toaster",
    "helicopter",
    "green beans",
    "brush",
    "carriage",
    "cigar",
    "earphone",
    "penguin",
    "hurdle",
    "swing",
    "radio",
    "cd",
    "parking meter",
    "swan",
    "garlic",
    "french fries",
    "horn",
    "avocado",
    "saxophone",
    "trumpet",
    "sandwich",
    "cue",
    "kiwi fruit",
    "bear",
    "fishing rod",
    "cherry",
    "tablet",
    "green vegetables",
    "nuts",
    "corn",
    "key",
    "screwdriver",
    "globe",
    "broom",
    "pliers",
    "volleyball",
    "hammer",
    "eggplant",
    "trophy",
    "dates",
    "board eraser",
    "rice",
    "tape measure/ruler",
    "dumbbell",
    "hamimelon",
    "stapler",
    "camel",
    "lettuce",
    "goldfish",
    "meat balls",
    "medal",
    "toothpaste",
    "antelope",
    "shrimp",
    "rickshaw",
    "trombone",
    "pomegranate",
    "coconut",
    "jellyfish",
    "mushroom",
    "calculator",
    "treadmill",
    "butterfly",
    "egg tart",
    "cheese",
    "pig",
    "pomelo",
    "race car",
    "rice cooker",
    "tuba",
    "crosswalk sign",
    "papaya",
    "hair drier",
    "green onion",
    "chips",
    "dolphin",
    "sushi",
    "urinal",
    "donkey",
    "electric drill",
    "spring rolls",
    "tortoise/turtle",
    "parrot",
    "flute",
    "measuring cup",
    "shark",
    "steak",
    "poker card",
    "binoculars",
    "llama",
    "radish",
    "noodles",
    "yak",
    "mop",
    "crab",
    "microscope",
    "barbell",
    "bread/bun",
    "baozi",
    "lion",
    "red cabbage",
    "polar bear",
    "lighter",
    "seal",
    "mangosteen",
    "comb",
    "eraser",
    "pitaya",
    "scallop",
    "pencil case",
    "saw",
    "table tennis paddle",
    "okra",
    "starfish",
    "eagle",
    "monkey",
    "durian",
    "game board",
    "rabbit",
    "french horn",
    "ambulance",
    "asparagus",
    "hoverboard",
    "pasta",
    "target",
    "hotair balloon",
    "chainsaw",
    "lobster",
    "iron",
    "flashlight",
)

_OBJECTS365_LABEL_ALIASES = {
    "barrel/bucket": "barrel",
    "blackboard/whiteboard": "blackboard",
    "bread/bun": "bun",
    "cabinet/shelf": "cabinet",
    "canned": "can",
    "cutting/chopping board": "cutting board",
    "glove": "gloves",
    "hamimelon": "hami melon",
    "hotair balloon": "hot-air balloon",
    "pen/pencil": "pen",
    "picture/frame": "picture",
    "pine apple": "pineapple",
    "pot/pan": "pot",
    "router/modem": "router",
    "shampoo/shower gel": "shampoo",
    "soccer": "soccer ball",
    "tape measure/ruler": "tape measure",
    "tortoise/turtle": "tortoise",
    "towel/napkin": "towel",
    "trash bin/can": "trash can",
    "wild bird": "bird",
}

OBJECTS365_CATEGORIES = tuple(
    _OBJECTS365_LABEL_ALIASES.get(label, label) for label in _OBJECTS365_MODEL_CATEGORIES
)


class Objects365ObjectDetector:
    """OpenCV adapter for a YOLO26 Objects365 one-to-many ONNX export."""

    image_size = 416

    def __init__(
        self,
        model: Path | None,
        *,
        categories: tuple[str, ...] = OBJECTS365_CATEGORIES,
        probability_threshold: float = 0.35,
        iou_threshold: float = 0.60,
        net: Network | None = None,
    ) -> None:
        if not categories:
            raise ValueError("Objects365 categories are required")
        self._categories = categories
        self._probability_threshold = probability_threshold
        self._iou_threshold = iou_threshold
        self._net = net if net is not None else cv2.dnn.readNetFromONNX(str(model))
        self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._output_names = tuple(self._net.getUnconnectedOutLayersNames())

    def detect(self, frame: npt.NDArray[np.uint8]) -> ObjectDetection:
        started = time.monotonic()
        original_height, original_width = frame.shape[:2]
        letterboxed, scale, left, top = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(
            letterboxed,
            scalefactor=1 / 255.0,
            size=(self.image_size, self.image_size),
            swapRB=True,
        )
        self._net.setInput(blob)
        outputs = self._net.forward(self._output_names)
        objects = self._decode(
            outputs,
            original_width=original_width,
            original_height=original_height,
            scale=scale,
            left=left,
            top=top,
        )
        return ObjectDetection(
            objects=objects,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    def _letterbox(
        self, frame: npt.NDArray[np.uint8]
    ) -> tuple[npt.NDArray[np.uint8], float, int, int]:
        height, width = frame.shape[:2]
        scale = min(self.image_size / width, self.image_size / height)
        resized_width = round(width * scale)
        resized_height = round(height * scale)
        resized = cv2.resize(
            frame,
            (resized_width, resized_height),
            interpolation=cv2.INTER_AREA,
        )
        left = (self.image_size - resized_width) // 2
        top = (self.image_size - resized_height) // 2
        letterboxed = cv2.copyMakeBorder(
            resized,
            top,
            self.image_size - resized_height - top,
            left,
            self.image_size - resized_width - left,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        return letterboxed, scale, left, top

    def _decode(
        self,
        outputs: tuple[npt.NDArray[np.float32], ...],
        *,
        original_width: int,
        original_height: int,
        scale: float,
        left: int,
        top: int,
    ) -> tuple[DetectedObject, ...]:
        if len(outputs) != 1:
            raise ValueError("Objects365 model must have one detection output")
        predictions = np.asarray(outputs[0])
        if predictions.ndim != 3 or predictions.shape[0] != 1:
            raise ValueError("Objects365 output shape is invalid")
        channel_count = len(self._categories) + 4
        if predictions.shape[1] == channel_count:
            predictions = predictions[0].T
        elif predictions.shape[2] == channel_count:
            predictions = predictions[0]
        else:
            raise ValueError("Objects365 model must use a one-to-many export")

        centers = predictions[:, :2]
        sizes = predictions[:, 2:4]
        class_scores = predictions[:, 4:]
        class_ids = np.argmax(class_scores, axis=1)
        confidences = np.max(class_scores, axis=1)
        candidate_indices = np.flatnonzero(confidences >= self._probability_threshold)
        if not len(candidate_indices):
            return ()

        boxes = np.column_stack(
            (
                centers[:, 0] - sizes[:, 0] / 2,
                centers[:, 1] - sizes[:, 1] / 2,
                sizes[:, 0],
                sizes[:, 1],
            )
        )
        selected: list[int] = []
        for class_id in np.unique(class_ids[candidate_indices]):
            same_class = candidate_indices[class_ids[candidate_indices] == class_id]
            kept = cv2.dnn.NMSBoxes(
                boxes[same_class].tolist(),
                confidences[same_class].tolist(),
                self._probability_threshold,
                self._iou_threshold,
            )
            selected.extend(same_class[np.asarray(kept).reshape(-1)].tolist())

        detected: list[DetectedObject] = []
        for index in sorted(
            selected,
            key=lambda item: float(confidences[item]),
            reverse=True,
        ):
            x, y, width, height = boxes[index]
            x1 = int(np.clip((x - left) / scale, 0, original_width))
            y1 = int(np.clip((y - top) / scale, 0, original_height))
            x2 = int(np.clip((x + width - left) / scale, 0, original_width))
            y2 = int(np.clip((y + height - top) / scale, 0, original_height))
            if x1 >= x2 or y1 >= y2:
                continue
            detected.append(
                DetectedObject(
                    label=self._categories[int(class_ids[index])],
                    confidence=round(float(confidences[index]), 6),
                    left=x1,
                    top=y1,
                    right=x2,
                    bottom=y2,
                )
            )
        return tuple(detected)
