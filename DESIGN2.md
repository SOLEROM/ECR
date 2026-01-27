The system is a controller-only experiment orchestration and recording framework intended for edge AI field experiments on embedded Linux targets.
It executes entirely on a control laptop and communicates with the target exclusively via SSH for command execution and SCP for artifact retrieval.
No resident agents, services, or libraries are required on the target beyond standard SSH access and existing runtime tools.
An experiment is modeled as a single run, optionally structured into ordered stages, but recorded as one atomic record.
Each run is instantiated from a target profile that defines connection parameters, supported actions, and command templates.
Actions are declarative, profile-defined operations composed of remote shell commands and file-pull steps.
All control operations are initiated from the controller and injected into the target environment remotely.
Execution results include stdout, stderr, exit codes, and timestamps for every invoked command.
System behavior and operator interactions are captured as an append-only, time-ordered event stream.
The event stream serves as the authoritative timeline and provenance record for the experiment.
Record immutability is enforced at the event level, with corrections represented as additional edit events rather than mutation.
Run deletion is supported as an explicit administrative operation.
Artifacts are limited to derived results and diagnostic outputs, excluding raw sensor or media streams.
All run data is stored locally in a deterministic, folder-per-run layout on the controller filesystem.
Each run folder contains a manifest describing metadata, parameters, and artifact references.
Background observation is supported through optional, low-frequency remote polling commands.
Background collectors are constrained by timeouts and execution limits to avoid operational interference.
The system is resilient to partial failures, including command errors and transient connectivity loss.
Failed actions are logged without invalidating the overall run record.
The web interface is bound to localhost and assumes a single trusted operator.
No authentication, encryption, or multi-user coordination mechanisms are included by design.
Time synchronization with the target is optional and not required for correctness.
At run completion, the full run directory can be packaged into a zip archive.
The exported archive constitutes a complete, self-describing experimental dataset.

