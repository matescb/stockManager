FROM node:26-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN n=0; \
    until npm ci --prefer-offline; do \
      n=$((n+1)); \
      if [ "$n" -ge 3 ]; then \
        echo "npm ci failed after 3 attempts" >&2; \
        exit 1; \
      fi; \
      sleep 5; \
    done

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
