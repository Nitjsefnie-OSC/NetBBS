#!/usr/bin/env node
/**
 * Small implementation-independent Link v1 checker.
 *
 * This file deliberately has no NetBBS imports or npm dependencies.  It is
 * a corpus consumer: the checked-in vectors are the contract, not this
 * implementation's private data model.
 */

import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readFile } from "node:fs/promises";

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const MIN_SAFE_INTEGER = Number.MIN_SAFE_INTEGER;

function normalize(value) {
  if (typeof value === "string") return value.normalize("NFC");
  if (Array.isArray(value)) return value.map(normalize);
  if (value !== null && typeof value === "object") {
    const result = {};
    const seen = new Set();
    for (const [key, item] of Object.entries(value)) {
      const normalizedKey = key.normalize("NFC");
      if (seen.has(normalizedKey)) {
        throw new Error(`normalization collision for object key ${JSON.stringify(normalizedKey)}`);
      }
      seen.add(normalizedKey);
      result[normalizedKey] = normalize(item);
    }
    return result;
  }
  if (typeof value === "number") {
    if (!Number.isInteger(value) || !Number.isSafeInteger(value)) {
      throw new Error("floats and unsafe integers are forbidden");
    }
  }
  return value;
}

function compareCodePoints(a, b) {
  const ac = Array.from(a);
  const bc = Array.from(b);
  const length = Math.min(ac.length, bc.length);
  for (let i = 0; i < length; i++) {
    const left = ac[i].codePointAt(0);
    const right = bc[i].codePointAt(0);
    if (left !== right) return left - right;
  }
  return ac.length - bc.length;
}

function escapeAscii(string) {
  let output = '"';
  for (const character of string) {
    const codePoint = character.codePointAt(0);
    if (character === '"') output += '\\"';
    else if (character === "\\") output += "\\\\";
    else if (codePoint === 0x08) output += "\\b";
    else if (codePoint === 0x0c) output += "\\f";
    else if (codePoint === 0x0a) output += "\\n";
    else if (codePoint === 0x0d) output += "\\r";
    else if (codePoint === 0x09) output += "\\t";
    else if (codePoint < 0x20 || codePoint > 0x7f) {
      if (codePoint <= 0xffff) output += `\\u${codePoint.toString(16).padStart(4, "0")}`;
      else {
        const adjusted = codePoint - 0x10000;
        const high = 0xd800 + (adjusted >> 10);
        const low = 0xdc00 + (adjusted & 0x3ff);
        output += `\\u${high.toString(16)}\\u${low.toString(16)}`;
      }
    } else output += character;
  }
  return `${output}"`;
}

function canonicalJson(value) {
  value = normalize(value);
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return escapeAscii(value);
  if (typeof value === "number") {
    if (value < MIN_SAFE_INTEGER || value > MAX_SAFE_INTEGER) throw new Error("unsafe integer");
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const keys = Object.keys(value).sort(compareCodePoints);
  return `{${keys.map((key) => `${escapeAscii(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

export function canonicalBytes(value) {
  return Buffer.from(canonicalJson(value), "utf8");
}

export function contentId(value) {
  return blake2b256(canonicalBytes(value)).toString("hex");
}

const BLAKE2B_IV = [
  0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn, 0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
  0x510e527fade682d1n, 0x9b05688c2b3e6c1fn, 0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n,
];
const BLAKE2B_SIGMA = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
  [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
  [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
  [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
  [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
  [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
  [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
  [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
  [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
  [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
  [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
];
const MASK64 = 0xffffffffffffffffn;
function rotr(x, n) { return ((x >> BigInt(n)) | (x << BigInt(64 - n))) & MASK64; }
function mix(v, a, b, c, d, x, y) {
  v[a] = (v[a] + v[b] + x) & MASK64; v[d] = rotr(v[d] ^ v[a], 32);
  v[c] = (v[c] + v[d]) & MASK64; v[b] = rotr(v[b] ^ v[c], 24);
  v[a] = (v[a] + v[b] + y) & MASK64; v[d] = rotr(v[d] ^ v[a], 16);
  v[c] = (v[c] + v[d]) & MASK64; v[b] = rotr(v[b] ^ v[c], 63);
}
function compress(h, block, blockOffset, counter, last) {
  const m = Array.from({ length: 16 }, (_, i) => block.readBigUInt64LE(blockOffset + i * 8));
  const v = [...h, ...BLAKE2B_IV];
  v[12] ^= BigInt(counter); if (last) v[14] ^= MASK64;
  for (const sigma of BLAKE2B_SIGMA) {
    mix(v, 0, 4, 8, 12, m[sigma[0]], m[sigma[1]]); mix(v, 1, 5, 9, 13, m[sigma[2]], m[sigma[3]]);
    mix(v, 2, 6, 10, 14, m[sigma[4]], m[sigma[5]]); mix(v, 3, 7, 11, 15, m[sigma[6]], m[sigma[7]]);
    mix(v, 0, 5, 10, 15, m[sigma[8]], m[sigma[9]]); mix(v, 1, 6, 11, 12, m[sigma[10]], m[sigma[11]]);
    mix(v, 2, 7, 8, 13, m[sigma[12]], m[sigma[13]]); mix(v, 3, 4, 9, 14, m[sigma[14]], m[sigma[15]]);
  }
  return h.map((x, i) => (x ^ v[i] ^ v[i + 8]) & MASK64);
}
function blake2b256(input) {
  let h = [...BLAKE2B_IV]; h[0] ^= 0x01010020n;
  let offset = 0;
  while (offset + 128 < input.length) { h = compress(h, input, offset, offset + 128, false); offset += 128; }
  const block = Buffer.alloc(128); input.copy(block, 0, offset);
  h = compress(h, block, 0, input.length, true);
  const output = Buffer.alloc(64); h.forEach((word, i) => output.writeBigUInt64LE(word, i * 8));
  return output.subarray(0, 32);
}

function publicKeyFromRaw(rawHex) {
  const raw = Buffer.from(rawHex, "hex");
  const spkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
  return createPublicKey({ key: Buffer.concat([spkiPrefix, raw]), format: "der", type: "spki" });
}

export function verifyVector(vector, publicKeyHex) {
  const bytes = canonicalBytes(vector.envelope);
  const signature = Buffer.from(vector.signature_hex, "hex");
  return verify(null, bytes, publicKeyFromRaw(publicKeyHex), signature);
}

export function parseWireJson(input) {
  const text = Buffer.isBuffer(input)
    ? new TextDecoder("utf-8", { fatal: true }).decode(input)
    : input;
  let position = 0;
  const whitespace = () => {
    while ([" ", "\t", "\r", "\n"].includes(text[position] ?? "")) position++;
  };
  const error = (message) => { throw new Error(`${message} at byte ${position}`); };
  const parseString = () => {
    const start = position++;
    let escaped = false;
    while (position < text.length) {
      const character = text[position++];
      if (escaped) { escaped = false; continue; }
      if (character === "\\") { escaped = true; continue; }
      if (character === '"') return JSON.parse(text.slice(start, position));
      if (character < " ") error("control character in string");
    }
    error("unterminated string");
  };
  const parseValue = () => {
    whitespace();
    const character = text[position];
    if (character === '"') return parseString();
    if (character === "{") {
      position++; const result = {}; const keys = new Set(); whitespace();
      if (text[position] === "}") { position++; return result; }
      while (true) {
        whitespace(); if (text[position] !== '"') error("object key must be a string");
        const key = parseString(); if (keys.has(key)) error(`duplicate object key ${key}`); keys.add(key);
        whitespace(); if (text[position++] !== ":") error("missing object colon");
        result[key] = parseValue(); whitespace();
        if (text[position] === "}") { position++; return result; }
        if (text[position++] !== ",") error("missing object comma");
      }
    }
    if (character === "[") {
      position++; const result = []; whitespace();
      if (text[position] === "]") { position++; return result; }
      while (true) {
        result.push(parseValue()); whitespace();
        if (text[position] === "]") { position++; return result; }
        if (text[position++] !== ",") error("missing array comma");
      }
    }
    if (text.startsWith("true", position)) { position += 4; return true; }
    if (text.startsWith("false", position)) { position += 5; return false; }
    if (text.startsWith("null", position)) { position += 4; return null; }
    const match = text.slice(position).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+|[eE][+-]?[0-9]+)?/);
    if (match) {
      const token = match[0]; position += token.length;
      if (!/^-?(?:0|[1-9][0-9]*)$/.test(token)) error("floating-point number is forbidden");
      const value = Number(token); if (!Number.isSafeInteger(value)) error("unsafe integer");
      return value;
    }
    error("invalid JSON value");
  };
  const result = parseValue(); whitespace();
  if (position !== text.length) error("trailing data");
  if (result === null || Array.isArray(result) || typeof result !== "object") error("wire object required");
  if (result.netbbs_protocol !== 1) error("unsupported protocol version");
  return result;
}

async function main() {
  const path = process.argv[2];
  if (!path) throw new Error("usage: node scripts/link_interop.mjs <vector-file>");
  const corpus = JSON.parse(await readFile(path, "utf8"));
  let failures = 0;
  const canonicalPath = new URL("../tests/fixtures/link_canonical_vectors.json", import.meta.url);
  const canonicalCorpus = JSON.parse(await readFile(canonicalPath, "utf8"));
  for (const vector of canonicalCorpus.vectors ?? []) {
    const actualHex = canonicalBytes(vector.fields).toString("hex");
    const actualId = contentId(vector.fields);
    if (actualHex !== vector.canonical_hex || actualId !== vector.content_id) {
      console.error(`FAIL canonical ${vector.name}`); failures++;
    } else console.log(`ok canonical ${vector.name}`);
  }
  for (const vector of corpus.signed_event_vectors ?? []) {
    const bytes = canonicalBytes(vector.envelope);
    const actualHex = bytes.toString("hex");
    const actualId = contentId(vector.envelope);
    const valid = verifyVector(vector, corpus.fixture_public_key_hex);
    if (actualHex !== vector.canonical_hex || actualId !== vector.content_id || valid !== vector.signature_valid) {
      console.error(`FAIL ${vector.name}`);
      failures++;
    } else console.log(`ok ${vector.name}`);
  }
  for (const vector of corpus.wire_vectors ?? []) {
    let accepted = false;
    try { parseWireJson(vector.json); accepted = true; } catch { /* expected corpus outcome */ }
    if (accepted !== vector.accepted) { console.error(`FAIL ${vector.name}`); failures++; }
    else console.log(`ok ${vector.name}`);
  }
  if (failures) process.exitCode = 1;
}

if (process.argv.length > 2) await main();
