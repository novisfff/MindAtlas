#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, lstatSync } from "node:fs";
import path from "node:path";

const root = path.resolve(process.argv[2] ?? "dist");

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function filesAt(directory, relativeDirectory = "") {
  return readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const relative = relativeDirectory
        ? path.posix.join(relativeDirectory, entry.name)
        : entry.name;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) return filesAt(absolute, relative);
      if (!entry.isFile() || lstatSync(absolute).isSymbolicLink()) {
        throw new Error("frontend build contains a non-regular file");
      }
      const bytes = readFileSync(absolute);
      return [{ relative, bytes }];
    });
}

const normalized = filesAt(root)
  .sort((left, right) => left.relative.localeCompare(right.relative))
  .map(({ relative, bytes }) => `${relative}\0${digest(bytes)}\0${bytes.length}\n`)
  .join("");

process.stdout.write(`${digest(Buffer.from(normalized, "utf8"))}\n`);
