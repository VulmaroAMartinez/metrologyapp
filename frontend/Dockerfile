FROM node:24.21.0-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1

ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node . /app
USER node
EXPOSE 3000
# TEMPORAL (el commit del frontend lo reemplaza por Next.js)
CMD ["node", "stub_server.js"]
