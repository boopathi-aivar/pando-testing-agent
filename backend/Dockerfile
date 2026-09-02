FROM public.ecr.aws/lambda/python:3.11

# System deps needed by PyMuPDF (gcc-c++/make needed in case pip falls back to
# building PyMuPDF from source when no matching manylinux wheel is found)
RUN yum install -y gcc gcc-c++ make libffi-devel && yum clean all

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
# --prefer-binary: PyMuPDF's newest release only ships as an sdist for some
# platforms, which forces a from-source MuPDF build. Preferring binary wheels
# picks the newest version that has a prebuilt wheel instead.
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .

# Default handler — overridden per-function in template.yaml
CMD ["lambda_handler.api_handler"]
