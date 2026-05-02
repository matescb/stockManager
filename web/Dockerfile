FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN for i in 1 2 3; do npm ci --prefer-offline && break || sleep 5; done

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
