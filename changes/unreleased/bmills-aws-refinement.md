### Run bioAF on AWS

- bioAF can now be deployed on Amazon Web Services (AWS) as well as Google
  Cloud. On AWS, notebooks, work nodes, and custom environment images all work
  the same way they do on Google Cloud, including saving your notebook outputs
  back to storage when a session ends.

### Work nodes

- You can now start a work node straight from an experiment, even if that
  experiment isn't part of a project yet. Before, a work node always needed a
  project, so a standalone experiment couldn't use one.

### Environments

- When you build a conda environment, bioAF now checks the definition as soon as
  you save it. If it isn't a valid conda environment file (for example, a
  Dockerfile pasted in by mistake), you get a clear message right away instead
  of a confusing failure partway through the build.
