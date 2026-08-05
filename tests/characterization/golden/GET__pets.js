// Validação: o endpoint deve retornar HTTP 200.

pm.test("Status code é 200", function () {
    pm.response.to.have.status(200);
});
