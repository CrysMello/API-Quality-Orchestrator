// Validação: o endpoint deve retornar HTTP 201.

pm.test("Status code é 201", function () {
    pm.response.to.have.status(201);
});
