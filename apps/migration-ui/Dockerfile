FROM node:20-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_ACCELERATOR_URL=http://localhost:8080
ARG NEXT_PUBLIC_AGENT_URL=http://localhost:8090
ARG NEXT_PUBLIC_REQUIRE_AUTH=false
ENV NEXT_PUBLIC_ACCELERATOR_URL=$NEXT_PUBLIC_ACCELERATOR_URL
ENV NEXT_PUBLIC_AGENT_URL=$NEXT_PUBLIC_AGENT_URL
ENV NEXT_PUBLIC_REQUIRE_AUTH=$NEXT_PUBLIC_REQUIRE_AUTH
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
