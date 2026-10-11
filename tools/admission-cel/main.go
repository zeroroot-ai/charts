// Command admission-cel compiles the CEL of each ValidatingAdmissionPolicy in
// rendered manifests, with the compiler and the environment of the Kubernetes
// API server.
//
// `helm template`, the goldens and kubeconform accept any string as a CEL
// expression. Only the API server compiles it, so a policy that does not
// compile was found only when an install failed (charts#601). This tool runs
// the same compile offline, in the merge gate.
//
// Usage: admission-cel FILE...
//
// Each FILE holds YAML documents. The tool exits 1 when an expression does not
// compile, and also when the files hold no ValidatingAdmissionPolicy at all,
// so a render that lost its policies cannot pass in silence.
package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"

	admissionv1 "k8s.io/api/admissionregistration/v1"
	utilyaml "k8s.io/apimachinery/pkg/util/yaml"
	plugincel "k8s.io/apiserver/pkg/admission/plugin/cel"
	"k8s.io/apiserver/pkg/admission/plugin/policy/validating"
	"k8s.io/apiserver/pkg/admission/plugin/webhook/matchconditions"
	"k8s.io/apiserver/pkg/cel/environment"
)

const policyKind = "ValidatingAdmissionPolicy"

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: admission-cel FILE...")
		os.Exit(2)
	}
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(paths []string, stdout, stderr io.Writer) int {
	seen := map[[sha256.Size]byte]bool{}
	policies, findings := 0, 0
	for _, path := range paths {
		docs, err := readPolicies(path)
		if err != nil {
			fmt.Fprintf(stderr, "%s: %v\n", path, err)
			return 1
		}
		for _, d := range docs {
			if seen[d.digest] {
				continue
			}
			seen[d.digest] = true
			policies++
			for _, msg := range compile(&d.policy) {
				fmt.Fprintf(stderr, "%s: %s/%s: %s\n", path, policyKind, d.policy.Name, msg)
				findings++
			}
		}
	}
	if policies == 0 {
		fmt.Fprintf(stderr, "no %s found in %d file(s); nothing was compiled\n", policyKind, len(paths))
		return 1
	}
	if findings > 0 {
		fmt.Fprintf(stderr, "%d expression(s) do not compile in the API server\n", findings)
		return 1
	}
	fmt.Fprintf(stdout, "%d distinct %s(s) compile\n", policies, policyKind)
	return 0
}

type policyDoc struct {
	policy admissionv1.ValidatingAdmissionPolicy
	digest [sha256.Size]byte
}

func readPolicies(path string) ([]policyDoc, error) {
	f, err := os.Open(path) //nolint:gosec // the path is an argument of the make target
	if err != nil {
		return nil, fmt.Errorf("open: %w", err)
	}
	defer func() { _ = f.Close() }()

	reader := utilyaml.NewYAMLReader(bufio.NewReader(f))
	var out []policyDoc
	for {
		raw, err := reader.Read()
		if errors.Is(err, io.EOF) {
			return out, nil
		}
		if err != nil {
			return nil, fmt.Errorf("read a YAML document: %w", err)
		}
		js, err := utilyaml.ToJSON(raw)
		if err != nil {
			return nil, fmt.Errorf("convert a YAML document: %w", err)
		}
		var head struct {
			APIVersion string `json:"apiVersion"`
			Kind       string `json:"kind"`
		}
		if err := json.Unmarshal(js, &head); err != nil || head.Kind != policyKind {
			continue
		}
		if head.APIVersion != admissionv1.SchemeGroupVersion.String() {
			return nil, fmt.Errorf("%s with apiVersion %q: only %s is compiled",
				policyKind, head.APIVersion, admissionv1.SchemeGroupVersion)
		}
		var p admissionv1.ValidatingAdmissionPolicy
		if err := json.Unmarshal(js, &p); err != nil {
			return nil, fmt.Errorf("decode a %s: %w", policyKind, err)
		}
		out = append(out, policyDoc{policy: p, digest: sha256.Sum256(js)})
	}
}

// compile mirrors the create validation of the API server: one composited
// compiler per policy, the variables first and in order, then the match
// conditions, the validations, their message expressions and the audit
// annotations. NewExpressions is the stricter environment of a new object.
func compile(p *admissionv1.ValidatingAdmissionPolicy) []string {
	compiler, err := plugincel.NewCompositedCompiler(
		environment.MustBaseEnvSet(environment.DefaultCompatibilityVersion()))
	if err != nil {
		return []string{fmt.Sprintf("make the compiler: %v", err)}
	}
	opts := plugincel.OptionalVariableDeclarations{
		HasParams:     p.Spec.ParamKind != nil,
		HasAuthorizer: true,
	}
	mode := environment.NewExpressions

	var out []string
	check := func(field string, r plugincel.CompilationResult) {
		if r.Error != nil {
			out = append(out, fmt.Sprintf("%s: %s", field, r.Error.Detail))
		}
	}
	for i, v := range p.Spec.Variables {
		check(fmt.Sprintf("spec.variables[%d] (%s)", i, v.Name),
			compiler.CompileAndStoreVariable(&validating.Variable{Name: v.Name, Expression: v.Expression}, opts, mode))
	}
	for i, m := range p.Spec.MatchConditions {
		mc := matchconditions.MatchCondition(m)
		check(fmt.Sprintf("spec.matchConditions[%d] (%s)", i, m.Name),
			compiler.CompileCELExpression(&mc, opts, mode))
	}
	for i, v := range p.Spec.Validations {
		check(fmt.Sprintf("spec.validations[%d].expression", i),
			compiler.CompileCELExpression(&validating.ValidationCondition{Expression: v.Expression}, opts, mode))
		if v.MessageExpression != "" {
			check(fmt.Sprintf("spec.validations[%d].messageExpression", i),
				compiler.CompileCELExpression(&validating.MessageExpressionCondition{MessageExpression: v.MessageExpression}, opts, mode))
		}
	}
	for i, a := range p.Spec.AuditAnnotations {
		check(fmt.Sprintf("spec.auditAnnotations[%d] (%s)", i, a.Key),
			compiler.CompileCELExpression(&validating.AuditAnnotationCondition{Key: a.Key, ValueExpression: a.ValueExpression}, opts, mode))
	}
	return out
}
