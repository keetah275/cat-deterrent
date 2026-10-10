# Garden Cat Deterrent Prototype

## Purpose

Build a Raspberry Pi-based system to deter cats from using the garden as a toilet. The intended final system will detect a cat in the garden and trigger a pump to spray water as a deterrent.

This document records the project context for future development and assistant sessions. It describes the intended plan, not features that are already implemented.

## Current focus: object detection

Camera input must remain independent of image processing. The prototype supports
the Pi camera, an outdoor camera's RTSP stream, and saved JPEG/PNG images through
a shared frame-source interface. The outdoor IP camera is a Tapo C325WB, purchased
as the PoE version. It supports RTSP; its local IP address and camera account are
still to be configured. Current processing detects motion,
not cats. A separate saved-image YOLO detector now accepts original-resolution
images and records labels, confidence scores, and boxes; model accuracy still
needs validation on garden images. Live animal detection is not integrated yet.
A live animal detector must also accept single images and report labels,
confidence scores, and bounding boxes without requiring motion first.

The immediate goal is to test detection of objects in the garden using the Raspberry Pi as the brains of the system.

- Establish an image capture and object detection workflow.
- Check whether objects can be detected reliably in the garden.
- Review results under different lighting, weather, and garden conditions.
- Record missed detections and false detections to guide improvements.

At this stage, detection is the priority. Automatic pump activation is a later step.

## Planned development stages

### 1. Test basic detection

Choose and configure the image source and detection software. Run initial tests on garden images or video and inspect the results. The camera, Raspberry Pi model, and software stack are still to be confirmed.

### 2. Differentiate between animals

Extend testing to distinguish cats from other animals and from people or unrelated movement. Evaluate confidence thresholds and repeated detections before deciding that a cat is present.

### 3. Integrate the pump

Connect a suitable pump and switching interface to the Raspberry Pi. Test pump control manually before linking it to detection. The pump, power supply, switching hardware, nozzle, and water supply are still to be selected.

### 4. Test automatic deterrence

Combine cat detection with a short water spray. Start with supervised trials and tune the detection rules and spray settings based on observed behaviour.

## Intended system flow

Garden image or video → Raspberry Pi detection → animal classification → activation decision → pump → water spray

During the initial prototype, this flow ends at detection results for review.

## Proposed operating safeguards

These are design requirements to refine during pump integration:

- Use water as a gentle deterrent, with spray duration and pressure kept appropriate for that purpose.
- Avoid activation for people, other animals, or uncertain detections.
- Provide a way to disable automatic spraying and stop the pump manually.
- Limit each activation and add a cooldown between sprays.
- Default the pump to off at startup and when the software fails.
- Keep electronics and electrical connections protected from water and outdoor conditions.

## Open decisions

- Raspberry Pi model and available processing capacity.
- Camera or other image source, placement, and garden area to monitor.
- Detection software and model.
- Animal categories to distinguish and acceptable detection accuracy.
- Whether night-time detection is needed.
- What images, video, or detection logs to retain for testing.
- Pump hardware, control circuit, spray coverage, duration, and cooldown.

## Immediate next step

Confirm the Raspberry Pi and image source, then build a minimal detection test that produces results we can inspect before adding animal-specific decisions or pump control.
