FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 3000

# Dev server for Phase 0; switch to `npm run build && npm run start` for production.
CMD ["npm", "run", "dev"]
