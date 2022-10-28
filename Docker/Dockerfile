# Requires LocPy to build
FROM locchan:locpy
ARG VAULT_PATH
# Build WoW-AHF
RUN mkdir -p /opt/wowahf
ADD wowahf /opt/wowahf
ADD setup.py /opt/
ADD Docker/_build.sh /opt
RUN set -e && /bin/bash /opt/_build.sh
RUN ln -sf /usr/share/zoneinfo/Europe/Minsk /etc/localtime
RUN printf "#!/bin/sh\nwowahf.py ${VAULT_PATH}" > /bin/start_ahf && chmod +x /bin/start_ahf

ENTRYPOINT /bin/start_ahf
