// The application's main/bootstrap remains responsible for HTTP, Swagger and
// RabbitMQ. Only add the existing diagnostic module to the Nest module graph.
if (process.env.PILOT_INJECT_DIAGNOSTICS === '1' && process.env.DB_DIAGNOSTICS === '1') {
  const { Module } = require('@nestjs/common');
  const { NestFactory } = require('@nestjs/core');
  const { DiagnosticsModule } = require('../dist/diagnostics/diagnostics.module');
  const original = NestFactory.create;
  NestFactory.create = function (applicationModule, ...options) {
    class ObservedApplication {}
    Module({ imports: [applicationModule, DiagnosticsModule] })(ObservedApplication);
    return Reflect.apply(original, this, [ObservedApplication, ...options]);
  };
}
